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
import csv
import json
import datetime
import lz4.block
import os
import math
from pathlib import Path
from typing import Optional
from visual_mutation_catalog import load_visual_mutation_names

_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableView, QPushButton, QLabel, QFileDialog, QHeaderView,
    QAbstractItemView, QSplitter, QFrame, QDialog, QGridLayout, QSizePolicy,
    QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QToolButton,
    QTableWidget, QTableWidgetItem, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QComboBox, QMessageBox, QSpinBox, QProgressBar, QTabWidget,
)
from PySide6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
    QFileSystemWatcher, QItemSelectionModel, QSize, Signal, QRegularExpression, QTimer,
    QThread,
)
from PySide6.QtGui import (
    QColor, QBrush, QAction, QActionGroup, QPalette, QFont, QKeySequence, QFontMetrics,
    QDoubleValidator, QRegularExpressionValidator,
)

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

def _apply_font_offset_to_tree(root: Optional[QWidget], offset_px: int):
    """
    Walk the widget tree and adjust every hardcoded `font-size:Npx` in
    stylesheets by `offset_px`.  Each widget's *original* stylesheet is
    stored as the Qt dynamic property ``_orig_ss`` on first encounter so
    subsequent calls always scale from the original, not the already-scaled
    value.
    """
    if root is None:
        return
    min_px = max(8, _ACCESSIBILITY_MIN_FONT_PX + offset_px)
    for widget in [root] + root.findChildren(QWidget):
        style = widget.styleSheet()
        if not style or "font-size" not in style:
            continue
        orig = widget.property("_orig_ss")
        if orig is None:
            # Always snapshot the stylesheet before we ever modify it.
            # Recover the true original by stripping any previous offset that
            # was applied by a prior call (identified by checking the current
            # offset stored on the widget).
            widget.setProperty("_orig_ss", style)
            orig = style
        new_style = _FONT_SIZE_RE.sub(
            lambda m, _off=offset_px, _min=min_px: (
                f"{m.group(1)}{max(_min, int(m.group(2)) + _off)}{m.group(3)}"
            ),
            orig,
        ) if offset_px != 0 else orig
        if new_style != style:
            widget.setStyleSheet(new_style)


# ── Constants ─────────────────────────────────────────────────────────────────

STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]

APPDATA_SAVE_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Glaiel Games", "Mewgenics",
)
APPDATA_CONFIG_DIR = os.path.join(
    os.environ.get("APPDATA", str(Path.home())),
    "MewgenicsBreedingManager",
)
os.makedirs(APPDATA_CONFIG_DIR, exist_ok=True)
APP_CONFIG_PATH = os.path.join(APPDATA_CONFIG_DIR, "settings.json")
LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
_DEFAULT_LANGUAGE = "en"

_SUPPORTED_LANGUAGES = {
    _DEFAULT_LANGUAGE: "language.english",
    "zh_CN": "language.zh_cn",
    "ru": "language.russian",
}
_LOCALE_CACHE: dict[str, dict[str, str]] = {}
_CURRENT_LANGUAGE = _DEFAULT_LANGUAGE

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
    "Floor2_Large":   "Second Floor Right",
    "Floor2_Small":   "Second Floor Left",
    "Attic":          "Attic",
}

ROOM_COLORS = {
    "Floor1_Large":   QColor(60, 100, 180),    # blue
    "Floor1_Small":   QColor(100, 140, 200),   # light blue
    "Floor2_Large":   QColor(180, 100, 60),    # orange
    "Floor2_Small":   QColor(200, 140, 100),   # light orange
    "Attic":          QColor(120, 100, 180),   # purple
}

EXCEPTIONAL_SUM_THRESHOLD = 40
DONATION_SUM_THRESHOLD = 34
DONATION_MAX_TOP_STAT = 6

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
    "williamssyndrome":   "+10 Charisma, -5 Intelligence",
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


def _steam_library_paths() -> list[str]:
    candidates = [
        os.path.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            "Steam",
            "steamapps",
            "libraryfolders.vdf",
        ),
        os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "Steam",
            "steamapps",
            "libraryfolders.vdf",
        ),
    ]
    libraries: list[str] = []
    for vdf_path in candidates:
        if not os.path.exists(vdf_path):
            continue
        try:
            with open(vdf_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            for match in re.finditer(r'"path"\s+"([^"]+)"', content):
                path = match.group(1).replace("\\\\", "\\")
                if path not in libraries:
                    libraries.append(path)
        except Exception:
            continue
    return libraries


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
        os.makedirs(APPDATA_CONFIG_DIR, exist_ok=True)
        with open(APP_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except Exception:
        pass


def _load_locale_catalog(language: str) -> dict[str, str]:
    cached = _LOCALE_CACHE.get(language)
    if cached is not None:
        return cached
    path = os.path.join(LOCALES_DIR, f"{language}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        catalog = data if isinstance(data, dict) else {}
    except Exception:
        catalog = {}
    _LOCALE_CACHE[language] = catalog
    return catalog


def _saved_language() -> str:
    data = _load_app_config()
    value = data.get("language", _DEFAULT_LANGUAGE)
    return value if value in _SUPPORTED_LANGUAGES else _DEFAULT_LANGUAGE


def _set_saved_language(language: str):
    if language not in _SUPPORTED_LANGUAGES:
        return
    data = _load_app_config()
    data["language"] = language
    _save_app_config(data)


def _set_current_language(language: str):
    global _CURRENT_LANGUAGE
    _CURRENT_LANGUAGE = language if language in _SUPPORTED_LANGUAGES else _DEFAULT_LANGUAGE
    _load_locale_catalog(_DEFAULT_LANGUAGE)
    if _CURRENT_LANGUAGE != _DEFAULT_LANGUAGE:
        _load_locale_catalog(_CURRENT_LANGUAGE)


def _current_language() -> str:
    return _CURRENT_LANGUAGE


def _tr(key: str, default: Optional[str] = None, **kwargs) -> str:
    text = _load_locale_catalog(_CURRENT_LANGUAGE).get(key)
    if text is None and _CURRENT_LANGUAGE != _DEFAULT_LANGUAGE:
        text = _load_locale_catalog(_DEFAULT_LANGUAGE).get(key)
    if text is None:
        text = default if default is not None else key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text


def _language_label(language: str) -> str:
    return _tr(_SUPPORTED_LANGUAGES.get(language, "language.english"))


def _detail_breeding_button_text(is_blacklisted: bool) -> str:
    if is_blacklisted:
        return _tr("detail.button.exclude_from_breeding", default="✗ Exclude from Breeding")
    return _tr("detail.button.include_in_breeding", default="✓ Include in Breeding")


def _detail_priority_button_text(must_breed: bool) -> str:
    if must_breed:
        return _tr("detail.button.must_breed", default="★ Must Breed")
    return _tr("detail.button.normal_priority", default="☆ Normal Priority")


def _family_tree_generation_label(level: int) -> str:
    if level == 1:
        return _tr("family_tree.row.parents", default="PARENTS")
    if level == 2:
        return _tr("family_tree.row.grandparents", default="GRANDPARENTS")
    if level == 3:
        return _tr("family_tree.row.great_grandparents", default="GREAT-GRANDPARENTS")
    return _tr(
        "family_tree.row.x_great_grandparents",
        default="{count}x GREAT-GRANDPARENTS",
        count=level - 2,
    )


def _set_localized_toggle_button_label(btn: QPushButton, label_key: str, default_label: str):
    btn.setProperty("_label_key", label_key)
    btn.setProperty("_label_default", default_label)
    state = _tr("common.on", default="On") if btn.isChecked() else _tr("common.off", default="Off")
    label = _tr(label_key, default=default_label)
    btn.setText(_tr("bulk.label_template", default="{label}: {state}", label=label, state=state))


def _trait_display_label(field: str, value) -> str:
    code = _trait_label_from_value(field, value)
    if not code:
        return ""
    key_defaults = {
        "low": ("trait.level.low", "Low"),
        "average": ("trait.level.average", "Average"),
        "high": ("trait.level.high", "High"),
        "not": ("trait.level.not_inbred", "Not inbred"),
        "slightly": ("trait.level.slightly_inbred", "Slightly inbred"),
        "moderately": ("trait.level.moderately_inbred", "Moderately inbred"),
    }
    key, default_label = key_defaults.get(code, ("common.unknown", "Unknown"))
    return _tr(key, default=default_label)


def _sexuality_display_label(value: str) -> str:
    code = (value or "").strip().lower()
    if code in ("bi", "gay", "straight"):
        return _tr(f"calibration.sexuality.{code}", default=code)
    return value or ""


def _gender_display_label(value: str) -> str:
    code = (value or "").strip().lower()
    if code == "male":
        return _tr("common.gender.male", default="male")
    if code == "female":
        return _tr("common.gender.female", default="female")
    if code in ("", "?", "unknown"):
        return _tr("common.gender.unknown", default="?")
    return value or ""


def _font_size_offset_label(offset: int) -> str:
    if offset > 0:
        return f"+{offset}pt"
    if offset < 0:
        return f"{offset}pt"
    return _tr("menu.settings.font_default", default="default")


def _visual_part_label(group_key: str, default_label: Optional[str] = None) -> str:
    fallback = default_label or _VISUAL_MUTATION_PART_LABELS.get(group_key, group_key.title())
    return _tr(f"detail.appearance.part.{group_key}", default=fallback)


def _localized_room_display() -> dict[str, str]:
    return {
        "Floor1_Large": _tr("room.floor1_large"),
        "Floor1_Small": _tr("room.floor1_small"),
        "Floor2_Large": _tr("room.floor2_large"),
        "Floor2_Small": _tr("room.floor2_small"),
        "Attic": _tr("room.attic"),
    }


def _localized_status_abbrev() -> dict[str, str]:
    return {
        "In House": _tr("status.in_house"),
        "Adventure": _tr("status.adventure"),
        "Gone": _tr("status.gone"),
    }


def _saved_gpak_path() -> str:
    data = _load_app_config()
    value = data.get("gpak_path", "")
    return value.strip() if isinstance(value, str) else ""


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
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources.gpak"),
        "/mnt/c/Program Files (x86)/Steam/steamapps/common/Mewgenics/resources.gpak",
        "/mnt/c/Program Files/Steam/steamapps/common/Mewgenics/resources.gpak",
    ]
    candidates.extend(direct_paths)

    for library in _steam_library_paths():
        candidates.append(os.path.join(library, "steamapps", "common", "Mewgenics", "resources.gpak"))

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


_GPAK_SEARCH_PATHS = _candidate_gpak_paths()
_GPAK_PATH = next((p for p in _GPAK_SEARCH_PATHS if os.path.exists(p)), None)


def _reload_game_data():
    global _GPAK_SEARCH_PATHS, _GPAK_PATH, _ABILITY_DESC, _VISUAL_MUT_DATA
    _GPAK_SEARCH_PATHS = _candidate_gpak_paths()
    _GPAK_PATH = next((p for p in _GPAK_SEARCH_PATHS if os.path.exists(p)), None)
    _ABILITY_DESC = _load_ability_descriptions()
    _VISUAL_MUT_DATA = _load_visual_mut_data()


def _set_gpak_path(path: str):
    cleaned = path.strip()
    if not cleaned:
        return
    data = _load_app_config()
    data["gpak_path"] = cleaned
    _save_app_config(data)
    _reload_game_data()


def _set_save_dir(path: str):
    cleaned = path.strip()
    if not cleaned:
        return
    data = _load_app_config()
    data["save_dir"] = cleaned
    _save_app_config(data)


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


def _load_gpak_text_strings(file_obj, file_offsets: dict[str, tuple[int, int]]) -> dict[str, str]:
    import csv as _csv
    import io as _io

    strings: dict[str, str] = {}
    for fname, (csv_off, csv_sz) in file_offsets.items():
        if not (fname.startswith("data/text/") and fname.endswith(".csv")):
            continue
        file_obj.seek(csv_off)
        raw_csv = file_obj.read(csv_sz).decode("utf-8-sig", errors="replace")
        for row in _csv.reader(_io.StringIO(raw_csv)):
            if len(row) >= 2 and row[0] and not row[0].startswith("//"):
                strings[row[0]] = row[1]
    return strings


def _resolve_game_string(value: str, game_strings: dict[str, str]) -> str:
    resolved = value
    seen: set[str] = set()
    while resolved in game_strings and resolved not in seen:
        seen.add(resolved)
        nxt = game_strings[resolved].strip()
        if not nxt:
            break
        resolved = nxt
    return resolved


def _load_ability_descriptions() -> dict[str, str]:
    """
    Build {normalized_ability_id: english_desc} by reading ability/passive GON files
    and combined.csv from the game's gpak. Returns {} if gpak is unavailable.
    """
    if not _GPAK_PATH:
        return {}
    try:
        with open(_GPAK_PATH, "rb") as f:
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

            block_re = re.compile(r'^([A-Za-z]\w*)\s*\{', re.MULTILINE)
            desc_re = re.compile(r'^\s*desc\s+"([^"]*)"', re.MULTILINE)

            def _clean(text: str) -> str:
                text = re.sub(r'\[img:[^\]]+\]', '', text)
                text = re.sub(r'\[s:[^\]]*\]|\[/s\]', '', text)
                text = re.sub(r'\[c:[^\]]*\]|\[/c\]', '', text)
                return re.sub(r'\s+', ' ', text).strip()

            result: dict[str, str] = {}
            for fname, (foff, fsz) in file_offsets.items():
                if not (
                    (fname.startswith("data/abilities/") or fname.startswith("data/passives/"))
                    and fname.endswith(".gon")
                ):
                    continue
                f.seek(foff)
                content = f.read(fsz).decode("utf-8", errors="replace")
                for bm in block_re.finditer(content):
                    ability_id = bm.group(1)
                    block_start = bm.end()
                    depth, idx = 1, block_start
                    while idx < len(content) and depth > 0:
                        if content[idx] == '{':
                            depth += 1
                        elif content[idx] == '}':
                            depth -= 1
                        idx += 1
                    block = content[block_start:idx - 1]
                    dm = desc_re.search(block)
                    if not dm:
                        continue
                    desc_val = dm.group(1)
                    desc_val = _resolve_game_string(desc_val, game_strings)
                    if not desc_val or desc_val == "nothing":
                        continue
                    result[ability_id.lower()] = _clean(desc_val)
        return result
    except Exception:
        return {}


_ABILITY_DESC: dict[str, str] = {}

_MUTATION_DISPLAY_NAMES: dict[str, str] = {
    "twoedarm": "Two-Toed Arm",
    "twotoedarm": "Two-Toed Arm",
    "twoedleg": "Two-Toed Leg",
    "twotoedleg": "Two-Toed Leg",
    "conjoinedbody": "Conjoined Body",
    "lumpybody": "Lumpy Body",
    "malnourishedbody": "Malnourished Body",
    "turnersyndrome": "Turner Syndrome",
    "williamssyndrome": "Williams Syndrome",
    "birdbeakears": "Bird Beak Ears",
    "floppyears": "Floppy Ears",
    "inwardeyes": "Inward Eyes",
    "redeyes": "Red Eyes",
    "bushyeyebrow": "Bushy Eyebrow",
    "noeyebrows": "No Eyebrows",
    "conjoinedtwin": "Conjoined Twin",
    "bentleg": "Bent Leg",
    "duckleg": "Duck Leg",
    "bentarm": "Bent Arm",
    "nomouth": "No Mouth",
    "cleftlip": "Cleft Lip",
    "lumpytail": "Lumpy Tail",
    "notail": "No Tail",
    "tailsack": "Tail Sack",
    "etank": "E-Tank",
    "deathsdoor": "Death's Door",
    "mightofthemeek": "Might of the Meek",
    "minime": "Mini-Me",
    "jackofalltrades": "Jack of All Trades",
    "slowandsteady": "Slow and Steady",
    "huntersboon": "Hunter's Boon",
    "holymantle": "Holy Mantle",
    "pawmissile": "Paw Missile",
    "pawmissle": "Paw Missile",
    "butcherssoul": "Butcher's Soul",
    "clericsoul": "Cleric Soul",
    "druidsoul": "Druid Soul",
    "fighterssoul": "Fighter's Soul",
    "hunterssoul": "Hunter's Soul",
    "jesterssoul": "Jester's Soul",
    "magessoul": "Mage's Soul",
    "monkssoul": "Monk's Soul",
    "necromancerssoul": "Necromancer's Soul",
    "psychicssoul": "Psychic's Soul",
    "sorcerersoul": "Sorcerer Soul",
    "tankssoul": "Tank's Soul",
    "thiefsoul": "Thief Soul",
    "tinkerersoul": "Tinkerer Soul",
    "voidsoul": "Void Soul",
}

_ABILITY_KEY_ALIASES: dict[str, str] = {
    "holymantle": "holymantel",
    "pawmissle": "pawmissile",
}


def _mutation_display_name(name: str) -> str:
    """Return a human-readable display name for a mutation/ability identifier."""
    key = re.sub(r'[^a-z0-9]', '', name.lower())
    if key in _MUTATION_DISPLAY_NAMES:
        return _MUTATION_DISPLAY_NAMES[key]
    spaced = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name)
    spaced = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', ' ', spaced)
    if spaced == spaced.lower():
        return spaced.title()
    return spaced


def _ability_tip(name: str) -> str:
    """Return a tooltip description for an ability/mutation name, or '' if unknown."""
    key = re.sub(r'[^a-z0-9]', '', name.lower())
    key = _ABILITY_KEY_ALIASES.get(key, key)
    lookup = _ABILITY_LOOKUP.get(key, "")
    desc = _ABILITY_DESC.get(key, "")
    if lookup and desc and lookup.lower() != desc.lower():
        return f"{lookup}\n{desc}"
    return desc or lookup


def _read_db_key_candidates(raw: bytes, self_key: int, offsets: tuple[int, ...], base_offset: int = 0) -> list[int]:
    keys: list[int] = []
    for off in offsets:
        pos = base_offset + off
        if pos < 0 or pos + 4 > len(raw):
            continue
        try:
            value = struct.unpack_from('<I', raw, pos)[0]
        except Exception:
            continue
        if value in (0, 0xFFFF_FFFF) or value == self_key:
            continue
        if value not in keys:
            keys.append(value)
    return keys


def _abilities_tooltip(cat: "Cat") -> str:
    lines: list[str] = []
    for ability in cat.abilities:
        tip = _ability_tip(ability)
        lines.append(ability if not tip else f"{ability}\n{tip}")
    for passive in cat.passive_abilities:
        name = _mutation_display_name(passive)
        tip = _ability_tip(passive)
        lines.append(f"● {name}" if not tip else f"● {name}\n{tip}")
    for disorder in cat.disorders:
        name = _mutation_display_name(disorder)
        tip = _ability_tip(disorder)
        lines.append(f"⚠ {name}" if not tip else f"⚠ {name}\n{tip}")
    return "\n\n".join(lines)


def _mutations_tooltip(cat: "Cat") -> str:
    parts: list[str] = []
    for text, tip in cat.mutation_chip_items:
        parts.append(tip or text)
    for text, tip in getattr(cat, "defect_chip_items", []):
        parts.append(f"⚠ {tip or text}")
    return "\n\n".join(parts)


def _relations_summary(cat: "Cat") -> str:
    parts: list[str] = []
    if cat.lovers:
        parts.append("L: " + ", ".join(other.name for other in cat.lovers))
    if cat.haters:
        parts.append("H: " + ", ".join(other.name for other in cat.haters))
    return " | ".join(parts)


def _cat_base_sum(cat: "Cat") -> int:
    return int(sum(cat.base_stats.values()))


def _is_exceptional_breeder(cat: "Cat") -> bool:
    return _cat_base_sum(cat) >= EXCEPTIONAL_SUM_THRESHOLD


def _donation_candidate_base_reason(cat: "Cat") -> Optional[str]:
    if _is_exceptional_breeder(cat):
        return None
    total = _cat_base_sum(cat)
    top_stat = max(cat.base_stats.values()) if cat.base_stats else 0
    reasons: list[str] = []
    if total <= DONATION_SUM_THRESHOLD:
        reasons.append(_tr(
            "donation.reason.base_sum",
            default="base sum {total} <= {threshold}",
            total=total,
            threshold=DONATION_SUM_THRESHOLD,
        ))
    if top_stat <= DONATION_MAX_TOP_STAT:
        reasons.append(_tr(
            "donation.reason.top_stat",
            default="top base stat {value} <= {threshold}",
            value=top_stat,
            threshold=DONATION_MAX_TOP_STAT,
        ))
    aggression = cat.aggression
    if aggression is not None and aggression >= 0.66:
        reasons.append(_tr("donation.reason.high_aggression", default="high aggression"))
    if not reasons:
        return None
    if total > DONATION_SUM_THRESHOLD and top_stat > DONATION_MAX_TOP_STAT:
        return None
    return ", ".join(reasons)


def _donation_candidate_reason(cat: "Cat") -> Optional[str]:
    base_reason = _donation_candidate_base_reason(cat)
    if base_reason is None:
        return None
    if cat.must_breed:
        return _tr(
            "donation.reason.must_breed_suffix",
            default="{reason} (currently marked Must Breed)",
            reason=base_reason,
        )
    return base_reason


def _is_donation_candidate(cat: "Cat") -> bool:
    return _donation_candidate_base_reason(cat) is not None


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
        headline = _tr(
            "pair_breakpoint.headline.locks",
            default="Locks {stats}",
            stats=", ".join(locks),
        )
    elif can_hit:
        headline = _tr(
            "pair_breakpoint.headline.can_hit",
            default="Can hit 7 in {stats}",
            stats=", ".join(can_hit),
        )
    elif near_hit:
        headline = _tr(
            "pair_breakpoint.headline.near_hit",
            default="One step off in {stats}",
            stats=", ".join(near_hit),
        )
    else:
        headline = _tr("pair_breakpoint.headline.none", default="No immediate 7 breakpoints")

    hints: list[str] = []
    if locks:
        hints.append(_tr(
            "pair_breakpoint.hint.locks",
            default="This pair already guarantees 7s in {stats}.",
            stats=", ".join(locks),
        ))
    if can_hit:
        hints.append(_tr(
            "pair_breakpoint.hint.can_hit",
            default="High-roll path to 7 exists in {stats}.",
            stats=", ".join(can_hit),
        ))
    if near_hit:
        hints.append(_tr(
            "pair_breakpoint.hint.near_hit",
            default="Next breakpoint is close in {stats}: bring in another 7 or keep the strongest kitten.",
            stats=", ".join(near_hit),
        ))
    if stalled:
        hints.append(_tr(
            "pair_breakpoint.hint.stalled",
            default="These stats are still below the next breakpoint: {stats}.",
            stats=", ".join(stalled),
        ))
    if len(upgrade_now) >= 4:
        hints.append(_tr(
            "pair_breakpoint.hint.good_progression",
            default="Good progression pair: multiple stats can improve immediately.",
        ))
    elif len(upgrade_now) <= 1:
        hints.append(_tr(
            "pair_breakpoint.hint.weak_progression",
            default="Weak progression pair: very few stats can improve from the better parent.",
        ))

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


def _ability_effect_lines(cat: "Cat") -> list[str]:
    lines: list[str] = []
    for ability in cat.abilities:
        tip = _ability_tip(ability).strip()
        if tip:
            lines.append(f"{ability}: {tip}")
    for passive in cat.passive_abilities:
        name = _mutation_display_name(passive)
        tip = _ability_tip(passive).strip()
        if tip:
            lines.append(f"{name}: {tip}")
    return lines


def _mutation_effect_lines(cat: "Cat") -> list[str]:
    lines: list[str] = []
    for text, tip in cat.mutation_chip_items:
        cleaned = tip.strip()
        if not cleaned:
            continue
        if cleaned == text:
            lines.append(text)
        else:
            lines.append(cleaned.replace("\n", " | "))
    return lines


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


def _parse_mutation_gon(content: str, game_strings: dict[str, str], category: str) -> dict[int, tuple[str, str]]:
    """Parse a mutation GON file into {slot_id: (display_name, stat_desc)}.

    Covers normal mutations (300-699), birth defects (700-706, and the
    special -2 "completely missing part" defect stored as 0xFFFFFFFE in
    the T table), and special/rare mutations (750+).
    IDs < 300 are base appearance variants handled separately.
    """
    result: dict[int, tuple[str, str]] = {}
    csv_prefix = f"MUTATION_{category.upper()}_"

    def _extract_block(start_pos: int) -> tuple[str, int]:
        """Extract the brace-delimited block starting at start_pos (after '{')."""
        depth, end = 1, start_pos
        while end < len(content) and depth > 0:
            if content[end] == '{':
                depth += 1
            elif content[end] == '}':
                depth -= 1
            end += 1
        return content[start_pos:end - 1], end

    def _block_to_entry(slot_id: int, block: str):
        """Parse a single mutation block into (display_name, stat_desc)."""
        name_match = re.search(r'//\s*(.+)', block)
        raw_name = name_match.group(1).strip().title() if name_match else f"Mutation {slot_id}"
        # Trim parenthetical dev comments, e.g., "No Eyes (Frame 703, ...)" → "No Eyes"
        raw_name = re.sub(r'\s*\(.*', '', raw_name).strip() or raw_name
        csv_key = f"{csv_prefix}{slot_id}_DESC"
        if csv_key in game_strings:
            stat_desc = _resolve_game_string(game_strings[csv_key], game_strings).strip().rstrip(".")
        else:
            header = block.split('{')[0]
            stats: list[str] = []
            for key, label in _STAT_LABELS.items():
                stat_match = re.search(rf'(?<!\w){re.escape(key)}\s+(-?\d+)', header)
                if stat_match:
                    value = int(stat_match.group(1))
                    stats.append(f"{'+' if value > 0 else ''}{value} {label}")
            stat_desc = ", ".join(stats)
        result[slot_id] = (raw_name, stat_desc)

    # ── Main numeric IDs (300+) ──────────────────────────────────────────
    # IDs < 300 are base appearance variants, not mutations — skip them.
    idx = 0
    while idx < len(content):
        match = re.search(r'(?<!\w)(\d{3,})\s*\{', content[idx:])
        if not match:
            break
        slot_id = int(match.group(1))
        block, idx = _extract_block(idx + match.end())
        if slot_id < 300:
            continue
        _block_to_entry(slot_id, block)

    # ── Special -2 entry ("completely missing part" birth defect) ────────
    # The GON files use `-2 {` for body parts that are entirely absent.
    # In the save's visual-mutation T table this is stored as the u32
    # value 0xFFFFFFFE (unsigned representation of -2).
    m2_match = re.search(r'(?<!\w)-2\s*\{', content)
    if m2_match:
        block, _ = _extract_block(m2_match.end())
        # Try the game-string key "MUTATION_EYES_M2_DESC" etc.
        csv_key_m2 = f"{csv_prefix}M2_DESC"
        if csv_key_m2 in game_strings:
            name_match = re.search(r'//\s*(.+)', block)
            raw_name = name_match.group(1).strip().title() if name_match else "Missing Part"
            # Trim parenthetical dev comments from the name
            raw_name = re.sub(r'\s*\(.*', '', raw_name).strip() or raw_name
            stat_desc = _resolve_game_string(game_strings[csv_key_m2], game_strings).strip().rstrip(".")
            result[0xFFFFFFFE] = (raw_name, stat_desc)
        else:
            _block_to_entry(0xFFFFFFFE, block)

    return result


def _load_visual_mut_data() -> dict[str, dict[int, tuple[str, str]]]:
    """Load {gon_category: {slot_id: (name, stat_desc)}} from resources.gpak."""
    if not _GPAK_PATH:
        return {}
    try:
        with open(_GPAK_PATH, "rb") as f:
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

            result: dict[str, dict[int, tuple[str, str]]] = {}
            for fname, (foff, fsz) in file_offsets.items():
                if not (fname.startswith("data/mutations/") and fname.endswith(".gon")):
                    continue
                category = fname.split("/")[-1].replace(".gon", "")
                f.seek(foff)
                content = f.read(fsz).decode("utf-8", errors="replace")
                result[category] = _parse_mutation_gon(content, game_strings, category)
        return result
    except Exception:
        return {}


_VISUAL_MUT_DATA = {}
_reload_game_data()


def _read_visual_mutation_entries(table: list[int]) -> list[dict[str, object]]:
    fallback_names = load_visual_mutation_names()
    entries: list[dict[str, object]] = []
    for slot_key, table_index, group_key, gpak_category, fallback_part, slot_label in _VISUAL_MUTATION_FIELDS:
        mutation_id = table[table_index] if table_index < len(table) else 0
        if mutation_id in (0, 0xFFFF_FFFF):
            continue

        # IDs < 300 are base appearance variants (normal cat looks), not mutations.
        # Actual mutations start at 300; birth defects are in the 700-706 range.
        # 0xFFFFFFFE (-2 as u32) = "completely missing part" birth defect.
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
        return [
            _tr(
                "detail.appearance.base_part",
                default="Base {part}",
                part=_visual_part_label(group_key),
            )
        ]
    return []


def _appearance_preview_text(a_names: list[str], b_names: list[str]) -> str:
    if not a_names and not b_names:
        return _tr("detail.appearance.no_data", default="No distinct appearance data")
    a_text = " / ".join(a_names) if a_names else _tr("detail.appearance.base", default="Base")
    b_text = " / ".join(b_names) if b_names else _tr("detail.appearance.base", default="Base")
    if set(a_names) == set(b_names):
        return _tr("detail.appearance.preview.likely", default="Likely {text}", text=a_text)
    return _tr(
        "detail.appearance.preview.probabilistic",
        default="Probabilistic: {a_text} or {b_text}",
        a_text=a_text,
        b_text=b_text,
    )


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


def _trait_inheritance_probabilities(
    a: 'Cat', b: 'Cat', stimulation: float,
) -> list[tuple[str, str, float, str]]:
    """
    Calculate per-trait inheritance probabilities using game formulas.
    Returns list of (display_name, category, probability, source_detail).

    Game formulas (from PurpleMyst's research):
    - Abilities: base_chance = 0.2 + 0.025 * stim, then diluted by pool size
    - Passives: base_chance = 0.05 + 0.01 * stim, then diluted by pool size
    - SkillShare+ parent: 100% for passives from that parent
    - Mutations: 80% base inheritance, favored by stimulation
    """
    stim = max(0.0, min(100.0, float(stimulation)))
    favor_weight = _stimulation_inheritance_weight(stim)
    results: list[tuple[str, str, float, str]] = []

    a_has_skillshare = any(p.lower() in ("skillshare", "skillshare+", "skillshareplus")
                          for p in (a.passive_abilities or []))
    b_has_skillshare = any(p.lower() in ("skillshare", "skillshare+", "skillshareplus")
                          for p in (b.passive_abilities or []))

    # ── Active abilities ──
    ability_base = 0.2 + 0.025 * stim
    a_abilities = list(a.abilities or [])
    b_abilities = list(b.abilities or [])
    seen: dict[str, tuple[float, str]] = {}
    b_keys = {x.lower() for x in b_abilities}
    a_keys = {x.lower() for x in a_abilities}

    for ab in a_abilities:
        key = ab.lower()
        prob_a = ability_base * favor_weight / len(a_abilities)
        if key in b_keys:
            prob_b = ability_base * (1.0 - favor_weight) / len(b_abilities)
            prob = min(1.0, prob_a + prob_b)
            seen[key] = (prob, f"Both parents ({prob*100:.0f}%)")
        else:
            seen[key] = (prob_a, f"From {a.name} ({prob_a*100:.0f}%)")

    for ab in b_abilities:
        key = ab.lower()
        if key not in seen:
            prob_b = ability_base * (1.0 - favor_weight) / len(b_abilities)
            seen[key] = (prob_b, f"From {b.name} ({prob_b*100:.0f}%)")

    for key, (prob, detail) in seen.items():
        display = key
        for ab in a_abilities + b_abilities:
            if ab.lower() == key:
                display = ab
                break
        results.append((display, "ability", prob, detail))

    # ── Passive abilities ──
    passive_base = 0.05 + 0.01 * stim
    a_passives = list(a.passive_abilities or [])
    b_passives = list(b.passive_abilities or [])
    seen_p: dict[str, tuple[float, str]] = {}
    b_pkeys = {x.lower() for x in b_passives}

    for pa in a_passives:
        key = pa.lower()
        if a_has_skillshare:
            prob = 1.0
            seen_p[key] = (prob, f"SkillShare+ from {a.name} (100%)")
        else:
            prob_a = passive_base * favor_weight / len(a_passives)
            if key in b_pkeys:
                prob_b = 1.0 if b_has_skillshare else passive_base * (1.0 - favor_weight) / len(b_passives)
                prob = min(1.0, prob_a + prob_b)
                seen_p[key] = (prob, f"Both parents ({prob*100:.0f}%)")
            else:
                seen_p[key] = (prob_a, f"From {a.name} ({prob_a*100:.0f}%)")

    for pa in b_passives:
        key = pa.lower()
        if key not in seen_p:
            if b_has_skillshare:
                seen_p[key] = (1.0, f"SkillShare+ from {b.name} (100%)")
            else:
                prob_b = passive_base * (1.0 - favor_weight) / len(b_passives)
                seen_p[key] = (prob_b, f"From {b.name} ({prob_b*100:.0f}%)")

    for key, (prob, detail) in seen_p.items():
        results.append((_mutation_display_name(key), "passive", prob, detail))

    # ── Mutations (visual) ──
    mutation_base = 0.80
    a_mutations = list(a.mutations or [])
    b_mutations = list(b.mutations or [])
    seen_m: dict[str, tuple[float, str]] = {}
    b_mkeys = {x.lower() for x in b_mutations}

    for mut in a_mutations:
        key = mut.lower()
        if key in b_mkeys:
            seen_m[key] = (mutation_base, f"Both parents ({mutation_base*100:.0f}%)")
        else:
            prob = mutation_base * favor_weight
            seen_m[key] = (prob, f"From {a.name} ({prob*100:.0f}%)")

    for mut in b_mutations:
        key = mut.lower()
        if key not in seen_m:
            prob = mutation_base * (1.0 - favor_weight)
            seen_m[key] = (prob, f"From {b.name} ({prob*100:.0f}%)")

    for key, (prob, detail) in seen_m.items():
        results.append((_mutation_display_name(key), "mutation", prob, detail))

    results.sort(key=lambda x: (-x[2], x[0].lower()))
    return results


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
        # This byte follows the optional post-name tag string, so use the
        # tag-aware anchor (personality_anchor), not name_end + fixed offset.
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
        # Libido and inbredness are doubles anchored after the post-name tag string.
        # Age is stored as creation_day at offset (blob_len - 103), then calculated as (current_day - creation_day).
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
                return None
            if not math.isfinite(v) or not (0.0 <= v <= 1.0):
                return None
            return float(v)

        self.libido = _read_personality(32)
        self.inbredness = _read_personality(40)
        self.aggression = _read_personality(64)

        # Parsed baseline values (before any manual calibration overrides).
        # NOTE: parsed_age is set after age extraction below.
        self.parsed_gender = self.gender
        self.parsed_aggression = self.aggression
        self.parsed_libido = self.libido
        self.parsed_inbredness = self.inbredness

        # Relationship slots: direct db_key references relative to the byte
        # immediately after the optional post-name tag string.
        self._lover_uids = _read_db_key_candidates(raw, self.db_key, (48,), base_offset=personality_anchor)
        self._hater_uids = _read_db_key_candidates(raw, self.db_key, (72,), base_offset=personality_anchor)
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

            # Passive1 is in run_items[10] (if the run is long enough)
            passives: list[str] = []
            for ri in run_items[10:]:
                if _valid_str(ri):
                    passives.append(ri)

            # After run: [u32 tier][string][u32 tier][string]...
            # Passive1 tier, then Passive2, Disorder1, Disorder2 each with tier.
            # Skip Passive1's tier first, then read 3 more string+tier pairs.
            try:
                r.u32()   # passive1 tier — discard
            except Exception:
                pass

            # Tail slots: index 0 = Passive2, indices 1–2 = Disorder1/Disorder2.
            # Passive2 goes into passives; disorders are kept separate so they
            # don't appear twice in the UI (once as ● passive, once as ⚠ disorder).
            disorders: list[str] = []
            for tail_idx in range(3):
                try:
                    item = r.str()
                except Exception:
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
                    break

            self.passive_abilities = passives
            self.disorders = disorders
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

        # Extract age from creation_day stored near the end of the blob (around blob_len - 103).
        # Search a small window around the typical offset to handle varying blob structures.
        if current_day is not None:
            try:
                # Try positions from blob_len-100 to blob_len-110, preferring closer to -103
                for offset_from_end in [103, 102, 104, 101, 105, 100, 106, 107, 108, 109, 110]:
                    pos = len(raw) - offset_from_end
                    if pos + 4 > len(raw) or pos < 0:
                        continue
                    creation_day = struct.unpack_from('<I', raw, pos)[0]
                    # Valid creation_day should be between 0 and current_day
                    if 0 <= creation_day <= current_day:
                        age = current_day - creation_day
                        # Accept if age is reasonable (0-100)
                        if 0 <= age <= 100:
                            self.age = age
                            break
            except Exception:
                pass

        self.parsed_age = self.age
        self.sexuality: str = "straight"  # bi / gay / straight — defaults to straight

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
    from collections import deque
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

    Processes cats in ascending generation order so each parent's paths are
    already in the memo when a child is processed.  This avoids re-traversing
    shared ancestry sub-trees: instead of O(n × 2^depth) work the total is
    proportional to the unique paths in the pedigree graph.

    Returns: {db_key: ancestor_paths_dict}
    """
    # Sort ascending by generation so founders (gen 0) come first.
    ordered = sorted(cats, key=lambda c: c.generation)

    # memo maps id(cat) -> that cat's ancestor-paths dict
    memo: dict[int, dict['Cat', list[tuple['Cat', ...]]]] = {}

    result: dict[int, dict['Cat', list[tuple['Cat', ...]]]] = {}

    for cat in ordered:
        # Start: cat reaches itself with a length-1 path
        paths: dict['Cat', list[tuple['Cat', ...]]] = {cat: [(cat,)]}

        for parent in (cat.parent_a, cat.parent_b):
            if parent is None:
                continue
            parent_paths = memo.get(id(parent))
            if parent_paths is None:
                # Parent absent from the ordered list (e.g. status "Gone") —
                # fall back to on-demand computation and cache it.
                parent_paths = _ancestor_paths(parent, max_steps)
                memo[id(parent)] = parent_paths

            for anc, path_list in parent_paths.items():
                for path in path_list:
                    # New path would be (cat,) + path; its step count = len(path).
                    if len(path) >= max_steps:
                        continue
                    new_path = (cat,) + path
                    paths.setdefault(anc, []).append(new_path)

        memo[id(cat)] = paths
        result[cat.db_key] = paths

    return result


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
    path from *cat* to that ancestor.  This is a compact float per ancestor
    instead of a list of full path tuples, and supports O(|common ancestors|)
    COI calculation via _coi_from_contribs().
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
    Batch-compute ancestor contribution dicts for all cats using a shared memo
    (same memoisation strategy as _build_ancestor_paths_batch, but storing
    floats instead of path tuples).  O(unique edges in pedigree graph) total
    instead of O(n × 2^depth).

    Returns: {db_key: {ancestor: float}}
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
        # Exclude self from result so COI computation doesn't count a cat as
        # its own ancestor.  Memo keeps self for correct child propagation.
        result[cat.db_key] = {k: v for k, v in contribs.items() if k is not cat}

    return result


def _coi_from_contribs(
    ca: dict['Cat', float],
    cb: dict['Cat', float],
) -> float:
    """
    Compute raw COI from two ancestor-contribution dicts.

    COI ≈ 0.5 × Σ_{A in common} ca[A] × cb[A]

    This approximates the full Wright path-coefficient formula without the
    path-overlap exclusion check.  For typical (non-extreme) pedigrees the
    result is identical; for heavily line-bred animals it may slightly
    overestimate, but for the UI's purposes (percentage risk) this is fine.
    Time: O(|common ancestors|), no path objects created.
    """
    if not ca or not cb:
        return 0.0
    coi = 0.0
    # Iterate over the smaller dict
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

    f(a, a) = (1 + F_a) / 2   where F_a = f(a.sire, a.dam)
    f(a, b) = (f(younger.sire, other) + f(younger.dam, other)) / 2

    Mathematically equivalent to Wright's path-coefficient COI but runs in
    O(unique ancestor pairs) instead of O(2^depth).
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
        # Recurse on the younger cat's parents (higher generation number)
        if a.generation > b.generation:
            result = (_kinship(a.parent_a, b, memo) + _kinship(a.parent_b, b, memo)) / 2.0
        else:
            result = (_kinship(a, b.parent_a, memo) + _kinship(a, b.parent_b, memo)) / 2.0
    memo[key] = result
    return result


def kinship_coi(a: Optional['Cat'], b: Optional['Cat'],
                memo: Optional[dict] = None) -> float:
    """
    COI of a hypothetical offspring of a × b, using memoised kinship.
    Pass a shared *memo* dict across multiple calls for O(1) amortised lookups.
    """
    if a is None or b is None:
        return 0.0
    if memo is None:
        memo = {}
    return _kinship(a, b, memo)


def _malady_breakdown(coi: float) -> tuple[float, float, float]:
    """
    Return (disorder_chance, part_defect_chance, combined_chance) from game logic.
    - Disorder: base 2%, scales above 0.20 CoI — chance of birth defect disorder
    - Part defect: 0 below 0.05 CoI, then 1.5×CoI — chance of mutated part defects
    - Combined: union probability of at least one occurring
    """
    disorder = 0.02 + 0.4 * min(max(coi - 0.20, 0.0), 1.0)
    defect = min(1.5 * coi, 1.0) if coi > 0.05 else 0.0
    combined = 1.0 - (1.0 - disorder) * (1.0 - defect)
    return disorder, defect, combined


def _combined_malady_chance(coi: float) -> float:
    """
    Probability that AT LEAST ONE birth defect occurs, from game logic.
    Combines disorder chance (base 2%, scales above 0.20 CoI) with
    part-defect chance (0 below 0.05 CoI, then 1.5×CoI).
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
        return False, _tr("breeding.reason.same_cat", default="Cannot pair a cat with itself")
    ga = (a.gender or "?").strip().lower()
    gb = (b.gender or "?").strip().lower()

    # Sexuality check — gay cats only pair with same gender, straight with opposite, bi with either.
    sa = (getattr(a, "sexuality", None) or "straight").lower()
    sb = (getattr(b, "sexuality", None) or "straight").lower()
    if ga != "?" and gb != "?":
        same_gender = ga == gb
        if sa == "gay" and not same_gender:
            return False, _tr(
                "breeding.reason.gay_same_gender",
                default="{name} is gay — needs same-gender partner",
                name=a.name,
            )
        if sb == "gay" and not same_gender:
            return False, _tr(
                "breeding.reason.gay_same_gender",
                default="{name} is gay — needs same-gender partner",
                name=b.name,
            )
        if sa == "straight" and same_gender:
            return False, _tr(
                "breeding.reason.straight_opposite_gender",
                default="{name} is straight — needs opposite-gender partner",
                name=a.name,
            )
        if sb == "straight" and same_gender:
            return False, _tr(
                "breeding.reason.straight_opposite_gender",
                default="{name} is straight — needs opposite-gender partner",
                name=b.name,
            )

    # Spidercat/unknown cats ('?') are allowed to pair with any gender.
    if ga == "?" or gb == "?":
        return True, ""
    # Bi cats can pair with any known gender.
    if sa == "bi" or sb == "bi":
        return True, ""
    # Gay cats confirmed same gender above — allow.
    if sa == "gay" or sb == "gay":
        return True, ""
    if ga != gb and {ga, gb} == {"male", "female"}:
        return True, ""
    # Same known sex (no sexuality override)
    if ga == "female" and gb == "female":
        return False, _tr("breeding.reason.both_female", default="Both cats are female — cannot produce offspring")
    if ga == "male" and gb == "male":
        return False, _tr("breeding.reason.both_male", default="Both cats are male — cannot produce offspring")
    return False, _tr("breeding.reason.incompatible_gender", default="Cats have incompatible genders — cannot produce offspring")


def _is_hater_pair(a: 'Cat', b: 'Cat') -> bool:
    return b in getattr(a, 'haters', []) or a in getattr(b, 'haters', [])


# ── Breeding cache (background pre-computation) ─────────────────────────────

def _breeding_cache_path(save_path: str) -> str:
    return save_path + ".breeding_cache.json"


class BreedingCache:
    """Pre-computed ancestry / risk data shared across all views."""

    def __init__(self):
        self.ready = False
        # Per-cat data  (keyed by db_key)
        self.ancestor_contribs: dict[int, dict['Cat', float]] = {}  # {ancestor: sum(0.5^d)}
        self.ancestor_depths: dict[int, dict['Cat', int]] = {}
        # Pairwise data  (keyed by (min_key, max_key))
        self.risk_pct: dict[tuple[int, int], float] = {}
        self.shared_counts: dict[tuple[int, int], tuple[int, int]] = {}
        # Cat lookup
        self._cats_by_key: dict[int, 'Cat'] = {}

    # ── disk persistence ──

    _CACHE_VERSION = 5  # bump to invalidate stale disk caches

    def save_to_disk(self, save_path: str):
        """Persist pairwise results alongside the save file."""
        data = {
            "version": self._CACHE_VERSION,
            "save_mtime": os.path.getmtime(save_path),
            "risk": {f"{a},{b}": v for (a, b), v in self.risk_pct.items()},
            "shared": {f"{a},{b}": list(v) for (a, b), v in self.shared_counts.items()},
        }
        try:
            with open(_breeding_cache_path(save_path), "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    @staticmethod
    def load_from_disk(save_path: str) -> Optional['BreedingCache']:
        """Load persisted pairwise data if the save file hasn't changed."""
        cp = _breeding_cache_path(save_path)
        if not os.path.exists(cp):
            return None
        try:
            with open(cp, "r") as f:
                data = json.load(f)
            if data.get("version") != BreedingCache._CACHE_VERSION:
                return None  # old format, recompute
            if abs(data.get("save_mtime", 0) - os.path.getmtime(save_path)) > 0.5:
                return None  # save file changed, cache is stale
            cache = BreedingCache()
            for k, v in data.get("risk", {}).items():
                a, b = k.split(",")
                cache.risk_pct[(int(a), int(b))] = float(v)
            for k, v in data.get("shared", {}).items():
                a, b = k.split(",")
                cache.shared_counts[(int(a), int(b))] = (int(v[0]), int(v[1]))
            # Mark as partially ready — pairwise data available, per-cat data needs recomputation
            cache.ready = True
            return cache
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None

    # ── public helpers ──

    @staticmethod
    def _pair_key(a_key: int, b_key: int) -> tuple[int, int]:
        return (a_key, b_key) if a_key < b_key else (b_key, a_key)

    def get_risk(self, a: 'Cat', b: 'Cat') -> float:
        if not self.ready:
            return risk_percent(a, b)
        return self.risk_pct.get(self._pair_key(a.db_key, b.db_key), 0.0)

    def get_shared(self, a: 'Cat', b: 'Cat', recent_depth: int = 3) -> tuple[int, int]:
        if not self.ready:
            return shared_ancestor_counts(a, b, recent_depth=recent_depth)
        return self.shared_counts.get(self._pair_key(a.db_key, b.db_key), (0, 0))

    def get_ancestor_depths_for(self, cat: 'Cat', max_depth: int = 8) -> dict['Cat', int]:
        if not self.ready:
            return _ancestor_depths(cat, max_depth=max_depth)
        return self.ancestor_depths.get(cat.db_key, {})


class BreedingCacheWorker(QThread):
    """Computes the full BreedingCache off the main thread."""
    progress = Signal(int, int)   # (current, total)
    phase1_ready = Signal(object)   # emits cache after phase 1 (ancestry only, no pairwise risk yet)
    finished_cache = Signal(object)  # emits the BreedingCache

    def __init__(self, cats: list['Cat'], save_path: str = "",
                 existing_pairwise: Optional['BreedingCache'] = None,
                 prev_cache: Optional['BreedingCache'] = None,
                 prev_parent_keys: Optional[dict[int, tuple]] = None,
                 parent=None):
        super().__init__(parent)
        self._cats = cats
        self._save_path = save_path
        self._existing = existing_pairwise  # disk-loaded cache with pairwise data only
        self._prev_cache = prev_cache       # previous in-memory cache for incremental update
        self._prev_parent_keys = prev_parent_keys or {}  # db_key -> (pa_key, pb_key) from prev load

    @staticmethod
    def _parent_key_tuple(cat: 'Cat') -> tuple:
        pa = cat.parent_a.db_key if cat.parent_a is not None else None
        pb = cat.parent_b.db_key if cat.parent_b is not None else None
        return (pa, pb)

    def run(self):
        alive = [c for c in self._cats if c.status != "Gone"]
        n = len(alive)

        has_pairwise = (
            self._existing is not None
            and self._existing.ready
            and len(self._existing.risk_pct) > 0
        )

        if has_pairwise:
            # Disk cache hit: pairwise data already loaded; only rebuild per-cat
            # ancestry (depths + contribs) for display / future incremental use.
            cache = self._existing
            cache._cats_by_key = {c.db_key: c for c in alive}
            self.progress.emit(0, n)
            batch = _build_ancestor_contribs_batch(alive)
            cache.ancestor_contribs.update(batch)
            for cat in alive:
                cache.ancestor_depths[cat.db_key] = _ancestor_depths(cat, max_depth=8)
            cache.ready = True
            self.progress.emit(n, n)
            self.finished_cache.emit(cache)
            return

        # ── Incremental mode: reuse unchanged cats from prev in-memory cache ──
        prev = self._prev_cache
        unchanged_keys: set[int] = set()
        alive_keys = {c.db_key for c in alive}
        if prev is not None and prev.ready and len(prev.risk_pct) > 0:
            for cat in alive:
                k = cat.db_key
                old_parents = self._prev_parent_keys.get(k)
                new_parents = self._parent_key_tuple(cat)
                if old_parents == new_parents and k in prev.ancestor_contribs:
                    unchanged_keys.add(k)
        else:
            prev = None

        changed_keys = alive_keys - unchanged_keys
        cache = BreedingCache()
        cache._cats_by_key = {c.db_key: c for c in alive}

        # ── Phase 1: per-cat ancestry (batch-memoized) ──
        # Reuse unchanged contribs / depths from prev
        if prev is not None:
            for k in unchanged_keys:
                cache.ancestor_contribs[k] = prev.ancestor_contribs[k]
                cache.ancestor_depths[k] = prev.ancestor_depths[k]

        # Count breedable pairs for progress (skip same-sex)
        def _can_possibly_breed(a: 'Cat', b: 'Cat') -> bool:
            ga, gb = a.gender, b.gender
            return not (ga == gb and ga != "?")

        n_phase2 = sum(
            1 for i in range(n) for j in range(i + 1, n)
            if alive[i].db_key not in unchanged_keys or alive[j].db_key not in unchanged_keys
            if _can_possibly_breed(alive[i], alive[j])
        )
        total_steps = max(1, n + n_phase2)
        self.progress.emit(0, total_steps)

        cats_to_compute = [c for c in alive if c.db_key in changed_keys]
        if cats_to_compute:
            # Include all alive cats so memo can traverse through unchanged parents
            batch = _build_ancestor_contribs_batch(alive)
            for cat in cats_to_compute:
                cache.ancestor_contribs[cat.db_key] = batch[cat.db_key]
                cache.ancestor_depths[cat.db_key] = _ancestor_depths(cat, max_depth=8)

        self.progress.emit(n, total_steps)

        # Emit phase1_ready so Safe Breeding / main table become usable now
        cache.ready = True  # ancestry complete; risk_pct still empty for dirty pairs
        self.phase1_ready.emit(cache)

        # ── Phase 2: pairwise risk + shared (skip same-sex, reuse unchanged) ──
        # Use path-based COI (with overlap exclusion) for correct results in
        # heavily inbred colonies.  Kinship is O(ancestor pairs) with memo
        # shared across all pair computations — orders of magnitude faster than
        # path enumeration for deep, inbred pedigrees.
        kinship_memo: dict[tuple[int, int], float] = {}

        pairs_to_compute = []
        for i in range(n):
            a = alive[i]
            for j in range(i + 1, n):
                b = alive[j]
                if not _can_possibly_breed(a, b):
                    continue
                if a.db_key in unchanged_keys and b.db_key in unchanged_keys:
                    pk = cache._pair_key(a.db_key, b.db_key)
                    old_risk = prev.risk_pct.get(pk) if prev else None
                    old_shared = prev.shared_counts.get(pk) if prev else None
                    if old_risk is not None and old_shared is not None:
                        cache.risk_pct[pk] = old_risk
                        cache.shared_counts[pk] = old_shared
                        continue
                pairs_to_compute.append((i, j))

        step = n
        for i, j in pairs_to_compute:
            a = alive[i]
            b = alive[j]
            pk = cache._pair_key(a.db_key, b.db_key)

            raw = _kinship(a, b, kinship_memo)
            cache.risk_pct[pk] = max(0.0, min(100.0, _combined_malady_chance(raw) * 100.0))

            da = cache.ancestor_depths.get(a.db_key, {})
            db_depths = cache.ancestor_depths.get(b.db_key, {})
            common = set(da.keys()) & set(db_depths.keys())
            if common:
                recent = sum(1 for anc in common if da[anc] <= 3 and db_depths[anc] <= 3)
                cache.shared_counts[pk] = (len(common), recent)
            else:
                cache.shared_counts[pk] = (0, 0)

            step += 1
            if step % 200 == 0:
                self.progress.emit(step, total_steps)

        self.progress.emit(total_steps, total_steps)
        if self._save_path:
            cache.save_to_disk(self._save_path)
        self.finished_cache.emit(cache)


class SaveLoadWorker(QThread):
    """Parses a save file off the main thread so the UI stays responsive."""
    status = Signal(str)  # status text updates
    finished_load = Signal(object)  # emits dict with parsed results

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        self.status.emit("Parsing save file…")
        cats, errors = parse_save(self._path)
        self.status.emit("Loading blacklist & overrides…")
        _load_blacklist(self._path, cats)
        _load_must_breed(self._path, cats)
        applied_overrides, override_rows = _load_gender_overrides(self._path, cats)
        cal_explicit, cal_token, cal_rows = _apply_calibration(self._path, cats)
        self.finished_load.emit({
            "cats": cats,
            "errors": errors,
            "applied_overrides": applied_overrides,
            "override_rows": override_rows,
            "cal_explicit": cal_explicit,
            "cal_token": cal_token,
            "cal_rows": cal_rows,
        })


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
    if _is_hater_pair(focus, other):
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
        cat_k, pa_k, pb_k, extra = struct.unpack_from('<QQQQ', data, pos)
        if cat_k == 0 or cat_k == NULL or cat_k > MAX_KEY:
            continue
        pa = int(pa_k) if pa_k != NULL and 0 < pa_k <= MAX_KEY else None
        pb = int(pb_k) if pb_k != NULL and 0 < pb_k <= MAX_KEY else None
        cat_key = int(cat_k)

        existing = ped_map.get(cat_key)
        if existing is None:
            # No entry yet — take whatever we have
            ped_map[cat_key] = (pa, pb)
        elif existing[0] is None or existing[1] is None:
            # Existing entry is incomplete — upgrade if this one is better
            if pa is not None and pb is not None:
                ped_map[cat_key] = (pa, pb)

    return ped_map


def parse_save(path: str) -> tuple[list, list]:
    conn  = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    house = _get_house_info(conn)
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

    # Build children bottom-up from the now-resolved parent fields.
    # This avoids the circular-reference problem in the pedigree blob.
    for cat in cats:
        cat.children = []
    for cat in cats:
        for parent in (cat.parent_a, cat.parent_b):
            if parent is not None and cat not in parent.children:
                parent.children.append(cat)

    # Compute generation depth safely (iterative; handles cycles)
    # Strays: generation 0
    for c in cats:
        c.generation = 0 if (c.parent_a is None and c.parent_b is None) else -1

    # Relaxation: propagate parent generations downward until stable
    for _ in range(len(cats) + 1):
        changed = False
        for c in cats:
            pa_g = c.parent_a.generation if c.parent_a is not None else -1
            pb_g = c.parent_b.generation if c.parent_b is not None else -1

            # If at least one parent has a known generation, we can set this cat's generation.
            if pa_g >= 0 or pb_g >= 0:
                g = max(pa_g, pb_g) + 1
                if c.generation != g:
                    c.generation = g
                    changed = True

        if not changed:
            break

    # Any remaining -1 are part of cycles or disconnected-from-stray components; default them to 0.
    for c in cats:
        if c.generation < 0:
            c.generation = 0

    return cats, errors


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


def _blacklist_path(save_path: str) -> str:
    """Return path for blacklist file associated with save."""
    return save_path + ".blacklist"


def _must_breed_path(save_path: str) -> str:
    """Return path for must-breed file associated with save."""
    return save_path + ".mustbreed"


def _gender_overrides_path(save_path: str) -> str:
    """Return CSV path for manual gender overrides associated with save."""
    return save_path + ".gender_overrides.csv"


def _calibration_path(save_path: str) -> str:
    """Return JSON path for manual calibration data associated with save."""
    return save_path + ".calibration.json"


def _normalize_override_gender(value: Optional[str]) -> str:
    g = (value or "").strip().lower()
    if g in ("male", "m") or g.startswith("male"):
        return "male"
    if g in ("female", "f") or g.startswith("female"):
        return "female"
    if g in ("?", "unknown") or g.startswith("spidercat"):
        return "?"
    return ""


def _load_gender_overrides(save_path: str, cats: list[Cat]) -> tuple[int, int]:
    """
    Apply manual gender overrides from sidecar CSV.
    CSV columns (header required):
      - gender (required)
      - unique_id (preferred key, e.g. 0x1234abcd...)
      - name (fallback key when unique_id missing)
    Returns (applied, rows_read).
    """
    path = _gender_overrides_path(save_path)
    if not os.path.exists(path):
        return 0, 0

    by_uid: dict[str, Cat] = {str(c.unique_id).strip().lower(): c for c in cats if c.unique_id}
    by_name: dict[str, list[Cat]] = {}
    for c in cats:
        key = (c.name or "").strip().lower()
        if key:
            by_name.setdefault(key, []).append(c)

    applied = 0
    rows_read = 0
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return 0, 0

            for row in reader:
                rows_read += 1
                g = _normalize_override_gender(row.get("gender"))
                if not g:
                    continue

                uid = (row.get("unique_id") or "").strip().lower()
                name = (row.get("name") or "").strip().lower()

                target: Optional[Cat] = None
                if uid and uid in by_uid:
                    target = by_uid[uid]
                elif name:
                    matches = by_name.get(name, [])
                    if len(matches) == 1:
                        target = matches[0]

                if target is None:
                    continue

                if target.gender != g:
                    target.gender = g
                applied += 1
    except Exception:
        return 0, 0

    return applied, rows_read


def _safe_float(v):
    try:
        return float(v)
    except Exception:
        return None


_CALIBRATION_TRAIT_OPTIONS = {
    "aggression": ("average", "high", "low"),
    "libido": ("average", "high", "low"),
    "inbredness": ("not", "slightly", "moderately"),
}

_CALIBRATION_TRAIT_NUMERIC = {
    "aggression": {"low": 0.0, "average": 0.5, "high": 1.0},
    "libido": {"low": 0.0, "average": 0.5, "high": 1.0},
    "inbredness": {"not": 0.0, "slightly": 0.5, "moderately": 1.0},
}


def _normalize_trait_override(field: str, value) -> str:
    options = _CALIBRATION_TRAIT_OPTIONS.get(field)
    if not options:
        return ""
    txt = str(value or "").strip().lower()
    if not txt:
        return ""
    if txt in options:
        return txt
    if field in ("aggression", "libido"):
        aliases = {"avg": "average", "medium": "average", "med": "average", "mid": "average"}
        mapped = aliases.get(txt, "")
        if mapped:
            return mapped
    if field == "inbredness":
        aliases = {"none": "not", "no": "not", "medium": "slightly", "med": "slightly"}
        mapped = aliases.get(txt, "")
        if mapped:
            return mapped
    return ""


def _trait_numeric_override(field: str, value):
    label = _normalize_trait_override(field, value)
    if not label:
        return None
    return _CALIBRATION_TRAIT_NUMERIC[field][label]


def _trait_label_from_value(field: str, value) -> str:
    label = _normalize_trait_override(field, value)
    if label:
        return label
    n = _safe_float(value)
    if n is None:
        return ""
    if field in ("aggression", "libido"):
        if n <= 0.3333:
            return "low"
        if n <= 0.6667:
            return "average"
        return "high"
    if field == "inbredness":
        if n <= 0.3333:
            return "not"
        if n <= 0.6667:
            return "slightly"
        return "moderately"
    return ""


_TRAIT_LEVEL_COLORS = {
    "low": QColor(70, 150, 90),
    "not": QColor(70, 150, 90),
    "average": QColor(185, 145, 60),
    "slightly": QColor(185, 145, 60),
    "high": QColor(175, 80, 80),
    "moderately": QColor(175, 80, 80),
    "low to average": QColor(128, 148, 74),
    "average to high": QColor(180, 112, 70),
    "not to slightly": QColor(128, 148, 74),
    "slightly to moderately": QColor(180, 112, 70),
}


def _trait_level_color(text: str) -> QColor:
    return _TRAIT_LEVEL_COLORS.get(str(text or "").strip().lower(), QColor(80, 80, 95))


def _load_calibration_data(save_path: str) -> dict:
    path = _calibration_path(save_path)
    if not os.path.exists(path):
        return {"version": 1, "overrides": {}, "gender_token_map": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": 1, "overrides": {}, "gender_token_map": {}}
        data.setdefault("version", 1)
        data.setdefault("overrides", {})
        data.setdefault("gender_token_map", {})
        if not isinstance(data["overrides"], dict):
            data["overrides"] = {}
        if not isinstance(data["gender_token_map"], dict):
            data["gender_token_map"] = {}
        return data
    except Exception:
        return {"version": 1, "overrides": {}, "gender_token_map": {}}


def _save_calibration_data(save_path: str, data: dict) -> bool:
    path = _calibration_path(save_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=True)
        return True
    except Exception:
        return False


def _learn_gender_token_map(cats: list[Cat], overrides: dict) -> dict[str, str]:
    counts: dict[str, dict[str, int]] = {}
    for cat in cats:
        if getattr(cat, "gender_source", "") != "token_fallback":
            continue
        token = (getattr(cat, "gender_token", "") or "").strip().lower()
        uid = (cat.unique_id or "").strip().lower()
        if not token or not uid:
            continue
        ov = overrides.get(uid)
        if not isinstance(ov, dict):
            continue
        g = _normalize_override_gender(ov.get("gender"))
        if not g:
            continue
        bucket = counts.setdefault(token, {})
        bucket[g] = bucket.get(g, 0) + 1

    out: dict[str, str] = {}
    for token, bucket in counts.items():
        total = sum(bucket.values())
        if total <= 0:
            continue
        top_gender, top_count = max(bucket.items(), key=lambda kv: kv[1])
        # Keep mapping when strong majority or single clear sample.
        if top_count / total >= 0.80:
            out[token] = top_gender
    return out


def _apply_calibration_data(data: dict, cats: list[Cat]) -> tuple[int, int, int]:
    """
    Apply calibration payload to cats in memory.
    Returns (explicit_rows_applied, token_rows_applied, override_rows_present).
    """
    overrides = data.get("overrides", {}) if isinstance(data, dict) else {}
    token_map = data.get("gender_token_map", {}) if isinstance(data, dict) else {}
    if not isinstance(overrides, dict):
        overrides = {}
    if not isinstance(token_map, dict):
        token_map = {}

    # Normalize token map values.
    norm_token_map: dict[str, str] = {}
    for k, v in token_map.items():
        token = str(k).strip().lower()
        g = _normalize_override_gender(v)
        if token and g:
            norm_token_map[token] = g

    token_rows_applied = 0
    for cat in cats:
        if getattr(cat, "status", "") == "Gone":
            continue
        if getattr(cat, "gender_source", "") != "token_fallback":
            continue
        token = (getattr(cat, "gender_token", "") or "").strip().lower()
        mapped = norm_token_map.get(token, "")
        if mapped and cat.gender != mapped:
            cat.gender = mapped
            token_rows_applied += 1

    explicit_rows_applied = 0
    for cat in cats:
        if getattr(cat, "status", "") == "Gone":
            continue
        uid = (cat.unique_id or "").strip().lower()
        ov = overrides.get(uid)
        if not isinstance(ov, dict):
            continue

        touched = False
        g = _normalize_override_gender(ov.get("gender"))
        if g:
            if cat.gender != g:
                cat.gender = g
            touched = True

        for field in ("age", "aggression", "libido", "inbredness"):
            if field == "age":
                val = _safe_float(ov.get(field))
            else:
                val = _trait_numeric_override(field, ov.get(field))
            if val is not None:
                setattr(cat, field, val)
                touched = True

        sex = ov.get("sexuality", "")
        if sex in ("bi", "gay", "straight"):
            cat.sexuality = sex
            touched = True

        # Apply base stats overrides
        base_stats_override = ov.get("base_stats")
        if isinstance(base_stats_override, dict):
            for stat_name, stat_val in base_stats_override.items():
                if stat_name in cat.base_stats:
                    try:
                        val = int(stat_val)
                        if 0 <= val <= 20:
                            cat.base_stats[stat_name] = val
                            touched = True
                    except (ValueError, TypeError):
                        pass

        if touched:
            explicit_rows_applied += 1

    return explicit_rows_applied, token_rows_applied, len(overrides)


def _apply_calibration(save_path: str, cats: list[Cat]) -> tuple[int, int, int]:
    data = _load_calibration_data(save_path)
    return _apply_calibration_data(data, cats)


def _save_blacklist(save_path: str, cats: list[Cat]):
    """Save blacklisted cat unique IDs to file."""
    blacklist_file = _blacklist_path(save_path)
    blacklisted_uids = [c.unique_id for c in cats if c.is_blacklisted]
    try:
        with open(blacklist_file, 'w') as f:
            f.write('\n'.join(blacklisted_uids))
    except Exception:
        pass


def _load_blacklist(save_path: str, cats: list[Cat]):
    """Load blacklist and mark cats accordingly."""
    blacklist_file = _blacklist_path(save_path)
    if not os.path.exists(blacklist_file):
        return
    try:
        with open(blacklist_file, 'r') as f:
            blacklisted_uids = set(line.strip() for line in f if line.strip())
        for cat in cats:
            cat.is_blacklisted = cat.unique_id in blacklisted_uids
    except Exception:
        pass


def _save_must_breed(save_path: str, cats: list[Cat]):
    """Save must-breed cat unique IDs to file."""
    must_breed_file = _must_breed_path(save_path)
    must_breed_uids = [c.unique_id for c in cats if c.must_breed]
    try:
        with open(must_breed_file, 'w') as f:
            f.write('\n'.join(must_breed_uids))
    except Exception:
        pass


def _load_must_breed(save_path: str, cats: list[Cat]):
    """Load must-breed list and mark cats accordingly."""
    must_breed_file = _must_breed_path(save_path)
    if not os.path.exists(must_breed_file):
        return
    try:
        with open(must_breed_file, 'r') as f:
            must_breed_uids = set(line.strip() for line in f if line.strip())
        for cat in cats:
            cat.must_breed = cat.unique_id in must_breed_uids
    except Exception:
        pass


# ── Qt table model ────────────────────────────────────────────────────────────

COLUMNS: list[str] = []
COL_NAME  = 0
COL_AGE   = 1
COL_GEN   = 2
COL_ROOM  = 3
COL_STAT  = 4
COL_BL    = 5
COL_MB    = 6
STAT_COLS = list(range(7, 14))   # STR … LCK
COL_SUM   = 14
COL_ABIL  = 15
COL_MUTS  = 16
COL_RELNS = 17
COL_REL   = 18
COL_AGG   = 19
COL_LIB   = 20
COL_INBRD = 21
COL_SEXUALITY = 22
COL_GEN_DEPTH = 23
COL_SRC   = 24


def _refresh_localized_constants():
    global ROOM_DISPLAY, STATUS_ABBREV, COLUMNS
    ROOM_DISPLAY = _localized_room_display()
    STATUS_ABBREV = _localized_status_abbrev()
    COLUMNS = [
        _tr("table.column.name"),
        _tr("table.column.age"),
        _tr("table.column.gender"),
        _tr("table.column.room"),
        _tr("table.column.status"),
        _tr("table.column.blacklist"),
        _tr("table.column.must_breed"),
    ] + STAT_NAMES + [
        _tr("table.column.sum"),
        _tr("table.column.abilities"),
        _tr("table.column.mutations"),
        _tr("table.column.relations"),
        _tr("table.column.risk"),
        _tr("table.column.aggression"),
        _tr("table.column.libido"),
        _tr("table.column.inbred"),
        _tr("table.column.sexuality"),
        _tr("table.column.generation"),
        _tr("table.column.source"),
    ]


_set_current_language(_saved_language())
_refresh_localized_constants()

# Fixed pixel widths for narrow columns
_W_STATUS = 62
_W_STAT   = 34
_W_GEN    = 28
_W_RELNS  = 130
_W_REL    = 68
_W_TRAIT  = 70
_ZOOM_MIN = 70
_ZOOM_MAX = 200
_ZOOM_STEP = 10


class CatTableModel(QAbstractTableModel):
    blacklistChanged = Signal()

    def __init__(self):
        super().__init__()
        self._cats: list[Cat] = []
        self._focus_cat: Optional[Cat] = None
        self._show_lineage: bool = False
        self._relation_cache: dict[int, float] = {}
        self._compat_cache: dict[int, str] = {}
        self._inbred_score_cache: dict[int, int] = {}
        self._ancestor_ids_cache: dict[int, frozenset[int]] = {}
        self._parent_ids_cache: dict[int, frozenset[int]] = {}
        self._hater_ids_cache: dict[int, frozenset[int]] = {}
        self._breeding_cache: Optional[BreedingCache] = None

    def set_breeding_cache(self, cache: Optional['BreedingCache']):
        self._breeding_cache = cache
        self._relation_cache.clear()
        self._compat_cache.clear()
        # Fill deferred caches from breeding cache data
        if cache is not None and cache.ready:
            for cat in self._cats:
                depths = cache.ancestor_depths.get(cat.db_key, {})
                self._ancestor_ids_cache[id(cat)] = frozenset(
                    id(anc) for anc in depths if anc is not cat
                )
                if cat.parent_a is not None and cat.parent_b is not None:
                    da = cache.ancestor_depths.get(cat.parent_a.db_key, {})
                    db = cache.ancestor_depths.get(cat.parent_b.db_key, {})
                    self._inbred_score_cache[id(cat)] = len(set(da.keys()) & set(db.keys()))
                else:
                    self._inbred_score_cache[id(cat)] = 0
        if self._cats:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._cats) - 1, len(COLUMNS) - 1),
                [Qt.DisplayRole, Qt.UserRole, Qt.BackgroundRole, Qt.ForegroundRole],
            )

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
        self._compat_cache.clear()
        # Cheap caches — computed inline
        self._parent_ids_cache = {
            id(cat): frozenset(id(parent) for parent in get_parents(cat))
            for cat in cats
        }
        self._hater_ids_cache = {
            id(cat): frozenset(id(hater) for hater in getattr(cat, "haters", []))
            for cat in cats
        }
        # Expensive caches — start empty, filled by breeding cache or on-demand
        self._ancestor_ids_cache = {}
        self._inbred_score_cache = {}
        self.endResetModel()

    def set_focus_cat(self, cat: Optional[Cat]):
        if cat is self._focus_cat:
            return
        self._focus_cat = cat
        self._relation_cache.clear()
        self._compat_cache.clear()
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
        bc = self._breeding_cache
        if bc is not None and bc.ready:
            pct = bc.get_risk(self._focus_cat, cat)
        else:
            pct = risk_percent(self._focus_cat, cat)
        self._relation_cache[key] = pct
        return pct

    def _compat_for(self, cat: Cat) -> Optional[str]:
        if self._focus_cat is None or cat is self._focus_cat:
            return None
        focus = self._focus_cat
        key = id(cat)
        cached = self._compat_cache.get(key)
        if cached is not None:
            return cached

        ok, _ = can_breed(focus, cat)
        if not ok:
            compat = 'incompatible'
        else:
            focus_id = id(focus)
            cat_id = id(cat)
            focus_haters = self._hater_ids_cache.get(focus_id, frozenset())
            cat_haters = self._hater_ids_cache.get(cat_id, frozenset())
            focus_parents = self._parent_ids_cache.get(focus_id, frozenset())
            cat_parents = self._parent_ids_cache.get(cat_id, frozenset())
            focus_anc = self._ancestor_ids_cache.get(focus_id, frozenset())
            cat_anc = self._ancestor_ids_cache.get(cat_id, frozenset())

            if cat_id in focus_haters or focus_id in cat_haters:
                compat = 'incompatible'
            elif focus_id in cat_parents or cat_id in focus_parents:
                compat = 'incompatible'
            elif focus_anc & cat_anc:
                compat = 'risky'
            else:
                compat = 'ok'

        self._compat_cache[key] = compat
        return compat

    def _inbred_score_for(self, cat: Cat) -> int:
        return self._inbred_score_cache.get(id(cat), 0)

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
        is_exceptional = _is_exceptional_breeder(cat)
        donation_reason = _donation_candidate_reason(cat)
        is_donation = donation_reason is not None

        def _badge_background() -> Optional[QColor]:
            if is_exceptional:
                return QColor(24, 78, 48)
            if is_donation:
                return QColor(82, 52, 22)
            return None

        if role == Qt.DisplayRole:
            if col == COL_NAME:
                if is_exceptional:
                    return f"[EXC] {cat.name}"
                if is_donation:
                    return f"[DON] {cat.name}"
                return cat.name
            if col == COL_AGE:  return str(cat.age) if cat.age is not None else "—"
            if col == COL_GEN:  return cat.gender_display
            if col == COL_ROOM: return cat.room_display
            if col == COL_STAT: return STATUS_ABBREV.get(cat.status, cat.status)
            if col == COL_BL:   return "X" if cat.is_blacklisted else ""
            if col == COL_MB:   return "★" if cat.must_breed else ""
            if col in STAT_COLS:
                return str(cat.base_stats[STAT_NAMES[col - STAT_COLS[0]]])
            if col == COL_SUM:
                return str(sum(cat.base_stats.values()))
            if col == COL_MUTS:
                parts = [_mutation_display_name(m) for m in cat.mutations]
                if cat.defects:
                    parts += [f"⚠ {d}" for d in cat.defects]
                return ", ".join(parts)
            if col == COL_ABIL:
                parts = list(cat.abilities) + [f"● {_mutation_display_name(p)}" for p in cat.passive_abilities]
                if cat.disorders:
                    parts += [f"⚠ {_mutation_display_name(d)}" for d in cat.disorders]
                return ", ".join(parts)
            if col == COL_RELNS:
                return _relations_summary(cat) or "—"
            if col == COL_REL:
                if self._focus_cat is None:
                    return "—"
                return f"{int(round(self._relation_for(cat)))}%"
            if col == COL_GEN_DEPTH:
                return str(cat.generation)
            if col == COL_AGG:
                label = _trait_display_label("aggression", cat.aggression)
                return label if label else "—"
            if col == COL_LIB:
                label = _trait_display_label("libido", cat.libido)
                return label if label else "—"
            if col == COL_INBRD:
                label = _trait_display_label("inbredness", cat.inbredness)
                return label if label else "—"
            if col == COL_SEXUALITY:
                return _sexuality_display_label(getattr(cat, "sexuality", None) or "")
            if col == COL_SRC:
                pa, pb = cat.parent_a, cat.parent_b
                if pa is None and pb is None:
                    return _tr("detail.source.stray", default="Stray")
                def _pname(p):
                    if p.status != "Gone":
                        return p.name
                    return _tr("detail.source.parent_gone", default="{name} (gone)", name=p.name)
                return " × ".join(_pname(p) for p in (pa, pb) if p is not None)
        elif role == Qt.UserRole:
            if col == COL_NAME:
                return (cat.name or "").lower()
            if col in STAT_COLS:
                return cat.base_stats[STAT_NAMES[col - STAT_COLS[0]]]
            if col == COL_SUM:
                return sum(cat.base_stats.values())
            if col == COL_REL:
                return self._relation_for(cat) if self._focus_cat is not None else -1.0
            if col == COL_AGE:
                return cat.age if cat.age is not None else -1
            if col == COL_GEN_DEPTH:
                return cat.generation
            if col == COL_AGG:
                return cat.aggression if cat.aggression is not None else -1.0
            if col == COL_LIB:
                return cat.libido if cat.libido is not None else -1.0
            if col == COL_INBRD:
                return cat.inbredness if cat.inbredness is not None else -1.0
            if col == COL_SEXUALITY:
                return getattr(cat, "sexuality", None) or ""
            return self.data(index, Qt.DisplayRole)

        elif role == Qt.BackgroundRole:
            compat = self._compat_for(cat)
            # Suppress risky highlight when lineage features are off
            if compat == 'risky' and not self._show_lineage:
                compat = 'ok'
            if col in STAT_COLS:
                base_c = STAT_COLORS.get(cat.base_stats[STAT_NAMES[col - STAT_COLS[0]]], QColor(100, 100, 115))
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
            if col in (COL_AGG, COL_LIB, COL_INBRD):
                if col == COL_AGG:
                    base = _trait_level_color(_trait_label_from_value("aggression", cat.aggression))
                elif col == COL_LIB:
                    base = _trait_level_color(_trait_label_from_value("libido", cat.libido))
                else:
                    base = _trait_level_color(_trait_label_from_value("inbredness", cat.inbredness))
                if compat == 'incompatible':
                    return QBrush(QColor(base.red() // 4, base.green() // 4, base.blue() // 4))
                if compat == 'risky':
                    return QBrush(QColor(base.red() // 2, base.green() // 2, base.blue() // 2))
                return QBrush(base)
            if col in (COL_NAME, COL_SUM):
                badge = _badge_background()
                if badge is not None:
                    if compat == 'incompatible':
                        badge = QColor(badge.red() // 4, badge.green() // 4, badge.blue() // 4)
                    elif compat == 'risky':
                        badge = QColor(badge.red() // 2, badge.green() // 2, badge.blue() // 2)
                    return QBrush(badge)
            if compat == 'incompatible':
                return QBrush(QColor(18, 12, 14))
            if compat == 'risky':
                return QBrush(QColor(22, 18, 10))

        elif role == Qt.ForegroundRole:
            compat = self._compat_for(cat)
            # Suppress risky highlight when lineage features are off
            if compat == 'risky' and not self._show_lineage:
                compat = 'ok'
            if compat == 'incompatible':
                return QBrush(QColor(65, 55, 60))
            if compat == 'risky':
                return QBrush(QColor(130, 110, 60))
            if col in STAT_COLS or col == COL_STAT or col in (COL_AGG, COL_LIB, COL_INBRD, COL_NAME, COL_SUM):
                return QBrush(QColor(255, 255, 255))

        elif role == Qt.ToolTipRole:
            if col == COL_NAME:
                notes: list[str] = []
                if is_exceptional:
                    notes.append(_tr(
                        "table.tooltip.exceptional_breeder",
                        default="Exceptional breeder: base stat sum {value} >= {threshold}",
                        value=_cat_base_sum(cat),
                        threshold=EXCEPTIONAL_SUM_THRESHOLD,
                    ))
                if donation_reason:
                    notes.append(_tr(
                        "table.tooltip.donation_candidate",
                        default="Donation candidate: {reason}",
                        reason=donation_reason,
                    ))
                if notes:
                    return "\n".join(notes)
                return cat.name
            if col in STAT_COLS:
                n = STAT_NAMES[col - STAT_COLS[0]]
                b = cat.base_stats[n]
                t = cat.total_stats[n]
                extra = f"  (+{t - b})" if t != b else ""
                return f"{n}  base: {b}{extra}  |  total: {t}"
            if col == COL_ROOM:
                return cat.room
            if col == COL_BL:
                return _tr(
                    "table.tooltip.blacklist.excluded",
                    default="Excluded from breeding calculations",
                ) if cat.is_blacklisted else _tr(
                    "table.tooltip.blacklist.included",
                    default="Included in breeding calculations",
                )
            if col == COL_MB:
                return _tr(
                    "table.tooltip.must_breed.enabled",
                    default="Must breed - prioritized in optimization",
                ) if cat.must_breed else _tr(
                    "table.tooltip.must_breed.normal",
                    default="Normal breeding priority",
                )
            if col == COL_MUTS and (cat.mutations or cat.defects):
                return _mutations_tooltip(cat)
            if col == COL_ABIL and (cat.abilities or cat.passive_abilities or cat.disorders):
                return _abilities_tooltip(cat)
            if col == COL_RELNS and (cat.lovers or cat.haters):
                lines: list[str] = []
                if cat.lovers:
                    lines.append(_tr(
                        "table.tooltip.lovers",
                        default="Lovers: {names}",
                        names=", ".join(other.name for other in cat.lovers),
                    ))
                if cat.haters:
                    lines.append(_tr(
                        "table.tooltip.haters",
                        default="Haters: {names}",
                        names=", ".join(other.name for other in cat.haters),
                    ))
                return "\n".join(lines)
            if col == COL_AGG:
                if cat.aggression is None:
                    return _tr("table.tooltip.aggression.unknown", default="Aggression: unknown")
                return _tr(
                    "table.tooltip.aggression.value",
                    default="Aggression: {value:.3f} ({label})",
                    value=cat.aggression,
                    label=_trait_display_label("aggression", cat.aggression),
                )
            if col == COL_LIB:
                if cat.libido is None:
                    return _tr("table.tooltip.libido.unknown", default="Libido: unknown")
                return _tr(
                    "table.tooltip.libido.value",
                    default="Libido: {value:.3f} ({label})",
                    value=cat.libido,
                    label=_trait_display_label("libido", cat.libido),
                )
            if col == COL_INBRD:
                if cat.inbredness is None:
                    return _tr("table.tooltip.inbredness.unknown", default="Inbredness: unknown")
                return _tr(
                    "table.tooltip.inbredness.value",
                    default="Inbredness: {value:.3f} ({label})",
                    value=cat.inbredness,
                    label=_trait_display_label("inbredness", cat.inbredness),
                )
            if col == COL_SUM:
                notes: list[str] = [_tr(
                    "table.tooltip.base_stat_sum",
                    default="Base stat sum: {value}",
                    value=_cat_base_sum(cat),
                )]
                if is_exceptional:
                    notes.append(_tr(
                        "table.tooltip.exceptional_threshold",
                        default="Exceptional threshold: >= {threshold}",
                        threshold=EXCEPTIONAL_SUM_THRESHOLD,
                    ))
                if donation_reason:
                    notes.append(_tr(
                        "table.tooltip.donation_signal",
                        default="Donation signal: {reason}",
                        reason=donation_reason,
                    ))
                return "\n".join(notes)

        elif role == Qt.CheckStateRole:
            if col == COL_BL:
                return Qt.Checked if cat.is_blacklisted else Qt.Unchecked
            if col == COL_MB:
                return Qt.Checked if cat.must_breed else Qt.Unchecked

        elif role == Qt.TextAlignmentRole:
            if col in STAT_COLS or col in (COL_GEN, COL_STAT, COL_AGE, COL_BL, COL_MB, COL_SUM, COL_REL, COL_GEN_DEPTH, COL_AGG, COL_LIB, COL_INBRD, COL_SEXUALITY):
                return Qt.AlignCenter

        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if index.column() in (COL_BL, COL_MB):
            return base | Qt.ItemIsUserCheckable
        return base

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid():
            return False
        col = index.column()
        if col not in (COL_BL, COL_MB) or role != Qt.CheckStateRole:
            return False
        cat = self._cats[index.row()]
        new_state = (value == Qt.Checked)
        changed_indexes = [index]

        if col == COL_BL:
            if cat.is_blacklisted == new_state:
                return False
            cat.is_blacklisted = new_state
            if new_state and cat.must_breed:
                cat.must_breed = False
                changed_indexes.append(self.index(index.row(), COL_MB))
        elif col == COL_MB:
            if cat.must_breed == new_state:
                return False
            cat.must_breed = new_state
            if new_state and cat.is_blacklisted:
                cat.is_blacklisted = False
                changed_indexes.append(self.index(index.row(), COL_BL))

        for changed_index in changed_indexes:
            self.dataChanged.emit(changed_index, changed_index, [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole])
        self.blacklistChanged.emit()
        return True

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

    def _matches_text_filter(self, cat: Cat) -> bool:
        if not self._name_filter:
            return True

        terms = [cat.name]
        terms.extend(cat.abilities)
        terms.extend(cat.passive_abilities)
        terms.extend(_mutation_display_name(p) for p in cat.passive_abilities)
        terms.extend(cat.disorders)
        terms.extend(_mutation_display_name(d) for d in cat.disorders)
        terms.extend(cat.mutations)
        terms.extend(_mutation_display_name(m) for m in cat.mutations)
        terms.extend(cat.defects)
        terms.extend(text for text, _ in getattr(cat, "mutation_chip_items", []))
        terms.extend(text for text, _ in getattr(cat, "defect_chip_items", []))
        terms.extend(other.name for other in cat.lovers)
        terms.extend(other.name for other in cat.haters)
        terms.append(_relations_summary(cat))

        haystack = " ".join(
            str(term).lower()
            for term in terms
            if term
        )
        return self._name_filter in haystack

    def filterAcceptsRow(self, source_row, source_parent):
        cat = self.sourceModel().cat_at(source_row)
        if cat is None:
            return False
        if not self._matches_text_filter(cat):
            return False
        if self._room == "__all__":
            return True
        if self._room is None:
            return cat.status != "Gone"
        if self._room == "__exceptional__":
            return cat.status != "Gone" and _is_exceptional_breeder(cat)
        if self._room == "__donation__":
            return cat.status != "Gone" and _is_donation_candidate(cat)
        if self._room == "__gone__":
            return cat.status == "Gone"
        if self._room == "__adventure__":
            return cat.status == "Adventure"
        return cat.room == self._room


# ── Detail / breeding panel widgets ──────────────────────────────────────────

_CHIP_STYLE = ("QLabel { background:#252545; color:#ccc; border-radius:6px;"
               " padding:2px 7px; font-size:11px; }")
_DEFECT_CHIP_STYLE = ("QLabel { background:#3a1a1a; color:#e0a0a0; border-radius:6px;"
                      " padding:2px 7px; font-size:11px; }")
_SEC_STYLE  = "color:#555; font-size:10px; font-weight:bold; letter-spacing:1px;"
_NAME_STYLE = "color:#eee; font-size:13px; font-weight:bold;"
_META_STYLE = "color:#777; font-size:11px;"
_WARN_STYLE = "color:#e07050; font-size:11px; font-weight:bold;"
_SAFE_STYLE = "color:#50c080; font-size:11px;"
_ANCS_STYLE = "color:#aaa; font-size:11px;"
_PANEL_BG   = "background:#0a0a18; border-top:1px solid #1e1e38;"
_DETAIL_TEXT_STYLE = "color:#d7d7e6; font-size:11px;"
_NOTE_STYLE = "color:#666; font-size:10px;"


def _chip(text: str, tooltip: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_CHIP_STYLE)
    if tooltip:
        lbl.setToolTip(tooltip)
    return lbl

def _defect_chip(text: str, tooltip: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_DEFECT_CHIP_STYLE)
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


def _detail_text_block(lines: list[str], style: str = _DETAIL_TEXT_STYLE) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 2, 0, 0)
    layout.setSpacing(4)
    for line in lines:
        lbl = QLabel(line)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(style)
        layout.addWidget(lbl)
    return box


def _wrapped_chip_block(items, tooltip_fn=None, display_fn=None, max_per_row: int = 5) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    if not items:
        return box
    for start in range(0, len(items), max_per_row):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        for item in items[start:start + max_per_row]:
            if isinstance(item, tuple):
                text, tip = item
                tip = tip or (tooltip_fn(text) if tooltip_fn else "")
            else:
                text = display_fn(item) if display_fn else item
                tip = tooltip_fn(item) if tooltip_fn else ""
            row.addWidget(_chip(text, tip))
        row.addStretch()
        layout.addLayout(row)
    return box


class ChipRow(QWidget):
    def __init__(self, items, tooltip_fn=None, display_fn=None):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        for item in items:
            if isinstance(item, tuple):
                text, tip = item
                tip = tip or (tooltip_fn(text) if tooltip_fn else "")
            else:
                text = display_fn(item) if display_fn else item
                tip = tooltip_fn(item) if tooltip_fn else ""
            row.addWidget(_chip(text, tip))
        row.addStretch()


def _defect_chip_row(items, tooltip_fn=None) -> QWidget:
    """Like ChipRow but uses the reddish defect chip style."""
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(5)
    for item in items:
        if isinstance(item, tuple):
            text, tip = item
            tip = tip or (tooltip_fn(text) if tooltip_fn else "")
        else:
            text = item
            tip = tooltip_fn(item) if tooltip_fn else ""
        row.addWidget(_defect_chip(text, tip))
    row.addStretch()
    return w


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
        self._pair_stimulation: int = int(_load_app_config().get("pair_stimulation", 50) or 50)
        self._current_cats: list[Cat] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("QScrollArea { border:none; background:#0a0a18; }")
        self._content = QWidget()
        self._scroll.setWidget(self._content)
        outer.addWidget(self._scroll)

    def set_show_lineage(self, show: bool):
        self._show_lineage = show

    def show_cats(self, cats: list[Cat]):
        self._current_cats = list(cats)
        self._content = QWidget()
        self._scroll.setWidget(self._content)

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
        name_row.addWidget(nl); name_row.addWidget(gl); name_row.addStretch()
        id_col.addLayout(name_row)
        id_col.addWidget(QLabel(cat.room_display or "—", styleSheet=_META_STYLE))

        # Stats: compact grid with shared Base / Mod / Total row labels.
        id_col.addSpacing(4)
        stats_box = QWidget()
        stats_box.setStyleSheet("background:#101024; border:1px solid #1e1e38; border-radius:4px;")
        stats_grid = QGridLayout(stats_box)
        stats_grid.setContentsMargins(6, 4, 6, 4)
        stats_grid.setHorizontalSpacing(6)
        stats_grid.setVerticalSpacing(1)
        stats_box.setMinimumWidth(280)

        corner = QLabel("")
        corner.setStyleSheet("color:#888; font-size:9px;")
        stats_grid.addWidget(corner, 0, 0)
        stats_grid.setColumnMinimumWidth(0, 34)

        for col, stat_name in enumerate(STAT_NAMES, start=1):
            head = QLabel(stat_name)
            head.setStyleSheet("color:#888; font-size:9px; font-weight:bold;")
            head.setAlignment(Qt.AlignCenter)
            stats_grid.addWidget(head, 0, col)
            stats_grid.setColumnMinimumWidth(col, 28)

        for row, label in enumerate(("Base", "Mod", "Total"), start=1):
            row_lbl = QLabel(label)
            row_lbl.setStyleSheet("color:#777; font-size:9px; font-weight:bold;")
            stats_grid.addWidget(row_lbl, row, 0)

        for col, stat_name in enumerate(STAT_NAMES, start=1):
            base = cat.base_stats[stat_name]
            total = cat.total_stats[stat_name]
            delta = total - base
            delta_sign = "+" if delta > 0 else ""
            delta_color = "#5a9" if delta > 0 else ("#c55" if delta < 0 else "#888")
            base_bg = STAT_COLORS.get(base, QColor(45, 45, 60)).name()
            total_bg = STAT_COLORS.get(total, QColor(45, 45, 60)).name()

            base_lbl = QLabel(str(base))
            base_lbl.setStyleSheet(
                f"background:{base_bg}; color:#fff; font-size:9px; font-weight:bold;"
                "border-radius:3px; padding:1px 4px;"
            )
            base_lbl.setAlignment(Qt.AlignCenter)
            stats_grid.addWidget(base_lbl, 1, col)

            mod_lbl = QLabel(f"{delta_sign}{delta}")
            mod_lbl.setStyleSheet(
                f"background:{'#183820' if delta > 0 else ('#3a1818' if delta < 0 else '#101024')};"
                f"color:{delta_color}; font-size:9px; border-radius:3px; padding:1px 4px;"
            )
            mod_lbl.setAlignment(Qt.AlignCenter)
            stats_grid.addWidget(mod_lbl, 2, col)

            total_lbl = QLabel(str(total))
            total_lbl.setStyleSheet(
                f"background:{total_bg}; color:#fff; font-size:9px; font-weight:bold;"
                "border-radius:3px; padding:1px 4px;"
            )
            total_lbl.setAlignment(Qt.AlignCenter)
            stats_grid.addWidget(total_lbl, 3, col)

        id_col.addWidget(stats_box)

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
            tree_btn = QPushButton(_tr("detail.button.family_tree", default="Family Tree…"))
            tree_btn.setStyleSheet(
                "QPushButton { color:#5a8aaa; background:transparent; border:1px solid #252545;"
                " padding:3px 8px; border-radius:4px; font-size:10px; }"
                "QPushButton:hover { background:#131328; }")
            tree_btn.clicked.connect(lambda: LineageDialog(cat, self, navigate_fn=_navigate).exec())
            id_col.addWidget(tree_btn)

        # Blacklist toggle button
        blacklist_btn = QPushButton(_detail_breeding_button_text(cat.is_blacklisted))
        blacklist_btn.setStyleSheet(
            "QPushButton { color:#888; background:transparent; border:1px solid #252545;"
            " padding:3px 8px; border-radius:4px; font-size:10px; }"
            "QPushButton:hover { background:#131328; color:#ddd; }")
        def _toggle_blacklist():
            cat.is_blacklisted = not cat.is_blacklisted
            if cat.is_blacklisted:
                cat.must_breed = False
            blacklist_btn.setText(_detail_breeding_button_text(cat.is_blacklisted))
            must_breed_btn.setText(_detail_priority_button_text(cat.must_breed))
            mw = self.window()
            if hasattr(mw, "_source_model") and mw._source_model is not None:
                for row in range(mw._source_model.rowCount()):
                    if mw._source_model.cat_at(row) is cat:
                        idx_bl = mw._source_model.index(row, COL_BL)
                        idx_mb = mw._source_model.index(row, COL_MB)
                        mw._source_model.dataChanged.emit(idx_bl, idx_bl, [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole])
                        mw._source_model.dataChanged.emit(idx_mb, idx_mb, [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole])
                        # Emit blacklistChanged which will trigger _on_blacklist_changed
                        mw._source_model.blacklistChanged.emit()
                        break
        blacklist_btn.clicked.connect(_toggle_blacklist)
        id_col.addWidget(blacklist_btn)

        # Must breed toggle button
        must_breed_btn = QPushButton(_detail_priority_button_text(cat.must_breed))
        must_breed_btn.setStyleSheet(
            "QPushButton { color:#888; background:transparent; border:1px solid #252545;"
            " padding:3px 8px; border-radius:4px; font-size:10px; }"
            "QPushButton:hover { background:#131328; color:#ddd; }")
        def _toggle_must_breed():
            cat.must_breed = not cat.must_breed
            if cat.must_breed:
                cat.is_blacklisted = False
            must_breed_btn.setText(_detail_priority_button_text(cat.must_breed))
            blacklist_btn.setText(_detail_breeding_button_text(cat.is_blacklisted))
            mw = self.window()
            if hasattr(mw, "_source_model") and mw._source_model is not None:
                for row in range(mw._source_model.rowCount()):
                    if mw._source_model.cat_at(row) is cat:
                        idx_bl = mw._source_model.index(row, COL_BL)
                        idx_mb = mw._source_model.index(row, COL_MB)
                        mw._source_model.dataChanged.emit(idx_bl, idx_bl, [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole])
                        mw._source_model.dataChanged.emit(idx_mb, idx_mb, [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole])
                        # Emit blacklistChanged to save must_breed state
                        mw._source_model.blacklistChanged.emit()
                        break
        must_breed_btn.clicked.connect(_toggle_must_breed)
        id_col.addWidget(must_breed_btn)

        id_col.addStretch()
        root.addLayout(id_col)

        # Abilities
        if cat.abilities or cat.passive_abilities or cat.disorders:
            root.addWidget(_vsep())
            ab = QVBoxLayout(); ab.setSpacing(4)
            ab.addWidget(_sec(_tr("detail.section.abilities", default="ABILITIES")))
            ab.addWidget(ChipRow(cat.abilities, tooltip_fn=_ability_tip))
            if cat.passive_abilities:
                ab.addWidget(_sec(_tr("detail.section.passive", default="PASSIVE")))
                ab.addWidget(ChipRow(
                    cat.passive_abilities,
                    tooltip_fn=_ability_tip,
                    display_fn=lambda n: f"● {_mutation_display_name(n)}",
                ))
            if cat.disorders:
                ab.addWidget(_sec(_tr("detail.section.disorders", default="DISORDERS")))
                ab.addWidget(ChipRow(
                    cat.disorders,
                    tooltip_fn=_ability_tip,
                    display_fn=lambda n: f"⚠ {_mutation_display_name(n)}",
                ))
            ability_lines = _ability_effect_lines(cat)
            if ability_lines:
                ab.addWidget(_detail_text_block(ability_lines))
            elif not _GPAK_PATH:
                ab.addWidget(_detail_text_block(
                    [_tr(
                        "detail.status.ability_descriptions_unavailable",
                        default="Ability descriptions unavailable. Set MEWGENICS_GPAK_PATH or place resources.gpak next to the app.",
                    )],
                    style=_NOTE_STYLE,
                ))
            ab.addStretch()
            root.addLayout(ab)

        # Mutations
        if cat.mutations or cat.defects:
            root.addWidget(_vsep())
            mu = QVBoxLayout(); mu.setSpacing(4)
            if cat.mutations:
                mu.addWidget(_sec(_tr("detail.section.mutations", default="MUTATIONS")))
                mu.addWidget(ChipRow(cat.mutation_chip_items, tooltip_fn=_ability_tip))
                mutation_lines = _mutation_effect_lines(cat)
                if mutation_lines:
                    mu.addWidget(_detail_text_block(mutation_lines))
                elif not _GPAK_PATH:
                    mu.addWidget(_detail_text_block(
                        [_tr(
                            "detail.status.mutation_text_unavailable",
                            default="Mutation effect text unavailable. Set MEWGENICS_GPAK_PATH or place resources.gpak next to the app.",
                        )],
                        style=_NOTE_STYLE,
                    ))
            if cat.defects:
                mu.addWidget(_sec(_tr("detail.section.birth_defects", default="BIRTH DEFECTS")))
                mu.addWidget(_defect_chip_row(cat.defect_chip_items, tooltip_fn=_ability_tip))
            mu.addStretch()
            root.addLayout(mu)

        # Equipment
        if cat.equipment:
            root.addWidget(_vsep())
            eq = QVBoxLayout(); eq.setSpacing(4)
            eq.addWidget(_sec(_tr("detail.section.equipment", default="EQUIPMENT")))
            eq.addWidget(ChipRow(cat.equipment))
            eq.addStretch()
            root.addLayout(eq)

        # Ancestry
        parents = get_parents(cat)
        gparents = get_grandparents(cat)
        if parents:
            root.addWidget(_vsep())
            anc = QVBoxLayout(); anc.setSpacing(4)
            anc.addWidget(_sec(_tr("detail.section.lineage", default="LINEAGE")))

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
                rel.addWidget(_sec(_tr("detail.section.lovers", default="LOVERS")))
                rel.addWidget(ChipRow([c.name for c in cat.lovers]))
            if cat.haters:
                rel.addWidget(_sec(_tr("detail.section.haters", default="HATERS")))
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
            rl = QLabel(f"  {cat.room_display}" if cat.room_display else "")
            rl.setStyleSheet(_META_STYLE)
            hdr.addWidget(rl)
            if cat is not b:
                x = QLabel("×")
                x.setStyleSheet("color:#444; font-size:14px; padding:0 10px;")
                hdr.addWidget(x)

        hdr.addStretch()
        stim_lbl = QLabel(_tr("detail.label.stimulation", default="Stimulation"))
        stim_lbl.setStyleSheet(_META_STYLE)
        hdr.addWidget(stim_lbl)
        stim_box = QSpinBox()
        stim_box.setRange(0, 100)
        stim_box.setValue(max(0, min(100, int(self._pair_stimulation))))
        stim_box.setFixedWidth(64)
        stim_box.setStyleSheet(
            "QSpinBox { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:2px 6px; font-size:11px; }"
        )
        def _set_pair_stimulation(value: int):
            self._pair_stimulation = int(value)
            data = _load_app_config()
            data["pair_stimulation"] = self._pair_stimulation
            _save_app_config(data)
            if len(self._current_cats) >= 2:
                current_pair = list(self._current_cats[:2])
                QTimer.singleShot(0, lambda pair=current_pair: self.show_cats(pair))
        stim_box.valueChanged.connect(_set_pair_stimulation)
        hdr.addWidget(stim_box)
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
        sh = QLabel(_tr("table.column.sum", default="Sum"))
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
                off_lbl = QLabel(_tr("detail.label.offspring", default="Offspring"))
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

        # Inherited personality traits (based on parsed/calibrated parent values)
        trait_col = QVBoxLayout()
        trait_col.setSpacing(6)
        trait_col.addWidget(_sec(_tr("detail.section.inherited_traits", default="INHERITED TRAITS")))

        def _trait_text(field: str, value) -> str:
            label = _trait_display_label(field, value)
            return label if label else _tr("common.unknown", default="unknown")

        def _offspring_trait_text(field: str, va, vb) -> str:
            if va is None or vb is None:
                return _tr("common.unknown", default="unknown")
            lo = min(float(va), float(vb))
            hi = max(float(va), float(vb))
            lo_label = _trait_display_label(field, lo) or _tr("common.unknown", default="unknown")
            hi_label = _trait_display_label(field, hi) or _tr("common.unknown", default="unknown")
            if lo_label == hi_label:
                return lo_label
            return _tr("detail.trait.range", default="{low} to {high}", low=lo_label, high=hi_label)

        def _trait_chip(text: str) -> QLabel:
            chip = _chip(text)
            color = _trait_level_color(text)
            chip.setStyleSheet(
                f"QLabel {{ background:rgb({color.red()},{color.green()},{color.blue()}); "
                f"color:#fff; border-radius:6px; padding:2px 7px; font-size:11px; }}"
            )
            return chip

        for field, title in (
            ("aggression", _tr("detail.trait.aggression", default="Aggression")),
            ("libido", _tr("detail.trait.libido", default="Libido")),
            ("inbredness", _tr("detail.trait.inbredness", default="Inbredness")),
        ):
            va = getattr(a, field, None)
            vb = getattr(b, field, None)
            row = QHBoxLayout()
            row.setSpacing(5)
            row.addWidget(QLabel(f"{title}:", styleSheet="color:#555; font-size:10px;"))
            row.addWidget(_trait_chip(_trait_text(field, va)))
            row.addWidget(QLabel("x", styleSheet="color:#444; font-size:10px;"))
            row.addWidget(_trait_chip(_trait_text(field, vb)))
            row.addWidget(QLabel("->", styleSheet="color:#666; font-size:10px;"))
            row.addWidget(_trait_chip(_offspring_trait_text(field, va, vb)))
            row.addStretch()
            trait_col.addLayout(row)

        trait_col.addStretch()
        mid.addLayout(trait_col)
        mid.addWidget(_vsep())

        # Abilities column
        ab_col = QVBoxLayout()
        ab_col.setSpacing(6)
        ab_col.addWidget(_sec(_tr("detail.section.abilities", default="ABILITIES")))
        for cat in (a, b):
            if cat.abilities or cat.passive_abilities or cat.disorders:
                ab_col.addWidget(QLabel(f"{cat.name}:", styleSheet="color:#555; font-size:10px;"))
                ability_items = [(ab, _ability_tip(ab)) for ab in cat.abilities]
                ability_items.extend(
                    (f"● {_mutation_display_name(pa)}", _ability_tip(pa))
                    for pa in cat.passive_abilities
                )
                ability_items.extend(
                    (f"⚠ {_mutation_display_name(d)}", _ability_tip(d))
                    for d in cat.disorders
                )
                ab_col.addWidget(_wrapped_chip_block(ability_items, max_per_row=4))
        ab_col.addStretch()
        mid.addLayout(ab_col)
        mid.addWidget(_vsep())

        if a.mutations or b.mutations or a.defects or b.defects:
            mu_col = QVBoxLayout()
            mu_col.setSpacing(6)
            if a.mutations or b.mutations:
                mu_col.addWidget(_sec(_tr("detail.section.mutations", default="MUTATIONS")))
                for cat in (a, b):
                    if cat.mutations:
                        mu_col.addWidget(QLabel(f"{cat.name}:", styleSheet="color:#555; font-size:10px;"))
                        mu_col.addWidget(_wrapped_chip_block(cat.mutation_chip_items, max_per_row=3))
            if a.defects or b.defects:
                mu_col.addWidget(_sec(_tr("detail.section.birth_defects", default="BIRTH DEFECTS")))
                for cat in (a, b):
                    if cat.defects:
                        mu_col.addWidget(QLabel(f"{cat.name}:", styleSheet="color:#555; font-size:10px;"))
                        mu_col.addWidget(_defect_chip_row(cat.defect_chip_items, tooltip_fn=_ability_tip))
            mu_col.addStretch()
            mid.addLayout(mu_col)

        root.addLayout(mid)

        stim = float(self._pair_stimulation)
        active_candidates, share_a, share_b = _inheritance_candidates(
            list(a.abilities),
            list(b.abilities),
            stim,
        )
        passive_candidates, _, _ = _inheritance_candidates(
            list(a.passive_abilities),
            list(b.passive_abilities),
            stim,
            display_fn=_mutation_display_name,
        )
        breakpoint_info = _pair_breakpoint_analysis(a, b, stim)

        inh = QVBoxLayout()
        inh.setSpacing(6)
        inh.addWidget(_sec(_tr("detail.section.inheritance", default="INHERITANCE")))
        inh_note = QLabel(
            _tr(
                "detail.inheritance.note",
                default="Estimated at stimulation {stim}. Parent source weighting: {a_name} {a_pct:.0f}% / {b_name} {b_pct:.0f}%.",
                stim=int(stim),
                a_name=a.name,
                a_pct=share_a * 100,
                b_name=b.name,
                b_pct=share_b * 100,
            )
        )
        inh_note.setStyleSheet(_META_STYLE)
        inh_note.setWordWrap(True)
        inh.addWidget(inh_note)

        active_label = QLabel(_tr("detail.inheritance.active_candidates", default="Active spell candidates"), styleSheet="color:#555; font-size:10px;")
        inh.addWidget(active_label)
        if active_candidates:
            inh.addWidget(_wrapped_chip_block(active_candidates, max_per_row=5))
        else:
            inh.addWidget(QLabel(_tr("detail.inheritance.no_active_candidates", default="No active ability candidates."), styleSheet=_META_STYLE))

        passive_label = QLabel(_tr("detail.inheritance.passive_candidates", default="Passive candidates"), styleSheet="color:#555; font-size:10px;")
        inh.addWidget(passive_label)
        if passive_candidates:
            inh.addWidget(_wrapped_chip_block(passive_candidates, max_per_row=4))
        else:
            inh.addWidget(QLabel(_tr("detail.inheritance.no_passive_candidates", default="No passive candidates."), styleSheet=_META_STYLE))

        # ── Trait inheritance probabilities ──
        trait_probs = _trait_inheritance_probabilities(a, b, stim)
        if trait_probs:
            inh.addWidget(QLabel(_tr("detail.inheritance.trait_chances", default="Trait inheritance chances"), styleSheet="color:#555; font-size:10px;"))
            prob_chips: list[tuple[str, str]] = []
            for display, category, prob, detail in trait_probs:
                pct = prob * 100
                cat_label = {
                    "ability": _tr("detail.inheritance.category.spell", default="Spell"),
                    "passive": _tr("detail.inheritance.category.passive", default="Passive"),
                    "mutation": _tr("detail.inheritance.category.mutation", default="Mutation"),
                }.get(category, category)
                chip_text = f"{display} {pct:.0f}%"
                tip_text = f"[{cat_label}] {detail}\n{_ability_tip(display)}" if _ability_tip(display) else f"[{cat_label}] {detail}"
                prob_chips.append((chip_text, tip_text))
            inh.addWidget(_wrapped_chip_block(prob_chips, max_per_row=5))

        # ── Risk breakdown ──
        coi = kinship_coi(a, b)
        disorder_ch, part_defect_ch, combined_ch = _malady_breakdown(coi)
        risk_row = QHBoxLayout()
        risk_row.setSpacing(8)
        risk_row.addWidget(QLabel(_tr("detail.risk.label", default="Risk:"), styleSheet="color:#555; font-size:10px;"))

        def _risk_chip(text: str, value: float) -> QLabel:
            c = _chip(text)
            if value > 0.10:
                bg = "#6a2a2a"
            elif value > 0.03:
                bg = "#5a4a2a"
            else:
                bg = "#2a3a2a"
            c.setStyleSheet(
                f"QLabel {{ background:{bg}; color:#ddd; border-radius:6px;"
                f" padding:2px 7px; font-size:11px; }}")
            return c

        risk_row.addWidget(_risk_chip(_tr("detail.risk.disorder", default="Disorder {percent:.1f}%", percent=disorder_ch * 100), disorder_ch))
        risk_row.addWidget(_risk_chip(_tr("detail.risk.part_defect", default="Part defect {percent:.1f}%", percent=part_defect_ch * 100), part_defect_ch))
        risk_row.addWidget(_risk_chip(_tr("detail.risk.combined", default="Combined {percent:.1f}%", percent=combined_ch * 100), combined_ch))
        disorder_tip = QLabel("(?)")
        disorder_tip.setStyleSheet("color:#555; font-size:10px;")
        disorder_tip.setToolTip(
            _tr(
                "detail.risk.tooltip",
                default="Disorder: base 2%, scales above 0.20 CoI\nPart defect: 0 below 0.05 CoI, then 1.5x CoI\nCombined: chance of at least one occurring",
            )
        )
        risk_row.addWidget(disorder_tip)
        risk_row.addStretch()
        inh.addLayout(risk_row)

        root.addLayout(inh)

        # ── Breakpoints + appearance + lineage ─────────────────────────────
        bot = QHBoxLayout()
        bot.setSpacing(20)

        bp_col = QVBoxLayout()
        bp_col.setSpacing(6)
        bp_col.addWidget(_sec(_tr("detail.section.breakpoint_hints", default="BREAKPOINT HINTS")))
        bp_note = QLabel(
            _tr(
                "detail.breakpoint.note",
                default="{headline}  |  Sum range {lo}-{hi}  |  Expected avg {avg:.1f}",
                headline=breakpoint_info["headline"],
                lo=breakpoint_info["sum_range"][0],
                hi=breakpoint_info["sum_range"][1],
                avg=breakpoint_info["avg_expected"],
            )
        )
        bp_note.setStyleSheet(_DETAIL_TEXT_STYLE)
        bp_note.setWordWrap(True)
        bp_col.addWidget(bp_note)

        bp_table = QTableWidget(4, len(STAT_NAMES))
        bp_table.setHorizontalHeaderLabels(STAT_NAMES)
        bp_table.setVerticalHeaderLabels([
            _tr("detail.breakpoint.row.range", default="Range"),
            _tr("detail.breakpoint.row.expected", default="Exp"),
            _tr("detail.breakpoint.row.breakpoint", default="Breakpoint"),
            _tr("detail.breakpoint.row.hint", default="Hint"),
        ])
        bp_table.setSelectionMode(QAbstractItemView.NoSelection)
        bp_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        bp_table.setFocusPolicy(Qt.NoFocus)
        bp_table.setWordWrap(False)
        bp_table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:11px;
            }
            QTableWidget::item { padding:2px 4px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:4px 3px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:10px; font-weight:bold;
            }
        """)
        bp_hh = bp_table.horizontalHeader()
        for col in range(len(STAT_NAMES)):
            bp_hh.setSectionResizeMode(col, QHeaderView.Stretch)
        bp_vh = bp_table.verticalHeader()
        for row in range(4):
            bp_vh.setSectionResizeMode(row, QHeaderView.ResizeToContents)
        for col_idx, row in enumerate(breakpoint_info["rows"]):
            status_color = {
                "locked": QColor(98, 194, 135),
                "can hit 7": QColor(143, 201, 230),
                "one step off": QColor(216, 181, 106),
                "stalled": QColor(190, 145, 40),
            }.get(row["status"], QColor(120, 120, 135))
            range_item = QTableWidgetItem(f"{row['lo']}-{row['hi']}" if row["lo"] != row["hi"] else str(row["lo"]))
            exp_item = QTableWidgetItem(f"{row['expected']:.1f}")
            status_item = QTableWidgetItem(row["status"])
            hint_text = (
                _tr("detail.breakpoint.hint.lock", default="lock") if row["status"] == "locked"
                else _tr("detail.breakpoint.hint.seven_now", default="7 now") if row["status"] == "can hit 7"
                else _tr("detail.breakpoint.hint.next_up", default="next up") if row["status"] == "one step off"
                else _tr("detail.breakpoint.hint.needs_help", default="needs help")
            )
            hint_item = QTableWidgetItem(hint_text)
            for item in (range_item, exp_item, status_item, hint_item):
                item.setForeground(QBrush(status_color))
                item.setTextAlignment(Qt.AlignCenter)
            bp_table.setItem(0, col_idx, range_item)
            bp_table.setItem(1, col_idx, exp_item)
            bp_table.setItem(2, col_idx, status_item)
            bp_table.setItem(3, col_idx, hint_item)
        bp_table.resizeRowsToContents()
        bp_height = bp_table.horizontalHeader().height() + 4
        for row in range(bp_table.rowCount()):
            bp_height += bp_table.rowHeight(row)
        bp_height += 4
        bp_table.setFixedHeight(bp_height)
        bp_col.addWidget(bp_table)
        if breakpoint_info["hints"]:
            hints_lbl = QLabel("  |  ".join(breakpoint_info["hints"][:2]))
            hints_lbl.setStyleSheet(_META_STYLE)
            hints_lbl.setWordWrap(True)
            bp_col.addWidget(hints_lbl)
        bot.addLayout(bp_col, 2)
        bot.addWidget(_vsep())

        app_col = QVBoxLayout()
        app_col.setSpacing(6)
        app_col.addWidget(_sec(_tr("detail.section.appearance_preview", default="APPEARANCE PREVIEW")))
        app_note = QLabel(_tr("detail.appearance.note", default="Probabilistic preview from parent coat/body/part data."))
        app_note.setStyleSheet(_META_STYLE)
        app_note.setWordWrap(True)
        app_col.addWidget(app_note)

        appearance_groups = [
            ("fur", _tr("detail.appearance.part.fur", default="Fur")),
            ("body", _tr("detail.appearance.part.body", default="Body")),
            ("head", _tr("detail.appearance.part.head", default="Head")),
            ("tail", _tr("detail.appearance.part.tail", default="Tail")),
            ("ears", _tr("detail.appearance.part.ears", default="Ears")),
            ("eyes", _tr("detail.appearance.part.eyes", default="Eyes")),
            ("mouth", _tr("detail.appearance.part.mouth", default="Mouth")),
        ]
        shown_preview = False
        for group_key, title in appearance_groups:
            a_names = _appearance_group_names(a, group_key)
            b_names = _appearance_group_names(b, group_key)
            if not a_names and not b_names:
                continue
            shown_preview = True
            row = QHBoxLayout()
            row.setSpacing(5)
            row.addWidget(QLabel(f"{title}:", styleSheet="color:#555; font-size:10px;"))
            row.addWidget(_chip(" / ".join(a_names) if a_names else _tr("detail.appearance.base", default="Base")))
            row.addWidget(QLabel("x", styleSheet="color:#444; font-size:10px;"))
            row.addWidget(_chip(" / ".join(b_names) if b_names else _tr("detail.appearance.base", default="Base")))
            row.addWidget(QLabel("->", styleSheet="color:#666; font-size:10px;"))
            row.addWidget(_chip(_appearance_preview_text(a_names, b_names)))
            row.addStretch()
            app_col.addLayout(row)

        if not shown_preview:
            app_col.addWidget(QLabel(_tr("detail.appearance.none", default="No distinct parent appearance data detected."), styleSheet=_META_STYLE))

        app_col.addStretch()
        bot.addLayout(app_col, 1)
        if self._show_lineage:
            bot.addWidget(_vsep())

        if self._show_lineage:
            lc = QVBoxLayout()
            lc.setSpacing(3)
            lc.addWidget(_sec(_tr("detail.section.lineage", default="LINEAGE")))
            common    = find_common_ancestors(a, b)
            is_direct = (a in get_parents(b) or b in get_parents(a))
            is_haters = (b in getattr(a, 'haters', []) or a in getattr(b, 'haters', []))

            if is_haters:
                lc.addWidget(QLabel(_tr("detail.lineage.haters", default="⚠  These cats hate each other"), styleSheet=_WARN_STYLE))
            if is_direct:
                lc.addWidget(QLabel(_tr("detail.lineage.direct_parent_offspring", default="⚠  Direct parent/offspring"), styleSheet=_WARN_STYLE))
            elif common:
                lc.addWidget(QLabel(
                    _tr(
                        "detail.lineage.shared_ancestors",
                        default="⚠  {count} shared ancestors: {names}",
                        count=len(common),
                        names="  ·  ".join(c.short_name for c in common[:6]),
                    ),
                    styleSheet=_WARN_STYLE))
            elif get_parents(a) or get_parents(b):
                lc.addWidget(QLabel(_tr("detail.lineage.no_shared_ancestors", default="✓  No shared ancestors"), styleSheet=_SAFE_STYLE))
            else:
                lc.addWidget(QLabel(_tr("detail.lineage.unknown", default="—  Lineage unknown"), styleSheet=_META_STYLE))

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
        self.setWindowTitle(_tr("family_tree.title", name=cat.name))
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
                btn = QPushButton(_tr("family_tree.unknown"))
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

        make_gen_row(_tr("family_tree.grandparents"), grandparents)
        make_gen_row(_tr("family_tree.parents"),      parents)
        make_gen_row("",             [cat], highlight_all=True)
        if children:
            make_gen_row(_tr("family_tree.lineage_children"), children[:8])
            if len(children) > 8:
                outer.addWidget(
                    QLabel(_tr("family_tree.more_children", count=len(children)-8),
                           styleSheet="color:#444; font-size:10px; padding-left:100px;"))
        if grandchildren:
            unique_gc = list({id(g): g for g in grandchildren}.values())
            make_gen_row(_tr("family_tree.lineage_grandchildren"), unique_gc[:8])
            if len(unique_gc) > 8:
                outer.addWidget(
                    QLabel(_tr("family_tree.more_grandchildren", count=len(unique_gc)-8),
                           styleSheet="color:#444; font-size:10px; padding-left:100px;"))

        outer.addStretch()
        close_btn = QPushButton(_tr("family_tree.close"))
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
        lv.addWidget(QLabel(_tr("family_tree.cats"), styleSheet="color:#666; font-size:10px; font-weight:bold;"))
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self._all_btn = _sidebar_btn(_tr("family_tree.filter_all"))
        self._alive_btn = _sidebar_btn(_tr("family_tree.filter_alive"))
        self._all_btn.setCheckable(True)
        self._alive_btn.setCheckable(True)
        self._alive_btn.setChecked(True)
        self._all_btn.clicked.connect(lambda: self._set_alive_only(False))
        self._alive_btn.clicked.connect(lambda: self._set_alive_only(True))
        mode_row.addWidget(self._all_btn)
        mode_row.addWidget(self._alive_btn)
        lv.addLayout(mode_row)
        self._search = QLineEdit()
        self._search.setPlaceholderText(_tr("family_tree.search_placeholder"))
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
        self.retranslate_ui()
        _enforce_min_font_in_widget_tree(self)

    def retranslate_ui(self):
        self._cats_label.setText(_tr("family_tree.list_title", default="Cats"))
        self._all_btn.setText(_tr("family_tree.filter.all", default="All"))
        self._alive_btn.setText(_tr("family_tree.filter.alive", default="Alive"))
        self._search.setPlaceholderText(_tr("family_tree.search_placeholder", default="Search cat name…"))
        self._refresh_list()

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
            root.addWidget(QLabel(_tr("family_tree.no_match"), styleSheet="color:#666; font-size:12px;"))
            root.addStretch()
            return

        title = QLabel(_tr("family_tree.title", name=cat.name))
        title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
        root.addWidget(title)
        root.addWidget(QLabel(_tr("family_tree.click_hint"), styleSheet="color:#666; font-size:11px;"))

        def cat_box(c: Optional[Cat], highlight=False):
            if c is None:
                btn = QPushButton(_tr("family_tree.unknown"))
                btn.setEnabled(False)
                btn.setStyleSheet(
                    "QPushButton { color:#303040; font-size:10px; padding:7px 10px;"
                    " background:#0e0e1c; border:1px solid #18182a; border-radius:6px; }")
                return btn
            line2 = c.gender_display
            if c.room_display:
                line2 += f"  {c.room_display}"
            if c.status == "Gone":
                line2 += f"  ({_tr('status.gone')})"
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
                return _tr("family_tree.level.parents")
            if level == 2:
                return _tr("family_tree.level.grandparents")
            if level == 3:
                return _tr("family_tree.level.great_grandparents")
            return _tr("family_tree.level.n_great_grandparents", count=level - 2)

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
        label_texts = [
            _tr("family_tree.row.self", default="SELF"),
            _tr("family_tree.row.children", default="CHILDREN"),
            _tr("family_tree.row.grandchildren", default="GRANDCHILDREN"),
        ] + [
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
                    _tr(
                        "family_tree.more_in_generation",
                        default="… and {count} more in {label}",
                        count=len(level_nodes) - 12,
                        label=_ancestor_row_label(idx),
                    ),
                    styleSheet="color:#555; font-size:10px;"))
            add_arrow()
        add_generation_row(_tr("family_tree.row.self", default="SELF"), [cat], highlight_self=True)

        if children:
            add_arrow()
            add_generation_row(_tr("family_tree.row.children", default="CHILDREN"), children[:10])
            if len(children) > 10:
                root.addWidget(QLabel(_tr("family_tree.more_children_short", default="… and {count} more children", count=len(children) - 10), styleSheet="color:#555; font-size:10px;"))
        if grandchildren:
            add_arrow()
            add_generation_row(_tr("family_tree.row.grandchildren", default="GRANDCHILDREN"), grandchildren[:10])
            if len(grandchildren) > 10:
                root.addWidget(QLabel(_tr("family_tree.more_grandchildren_short", default="… and {count} more grandchildren", count=len(grandchildren) - 10), styleSheet="color:#555; font-size:10px;"))
        if not any([ancestor_levels, children, grandchildren]):
            root.addWidget(QLabel(_tr("family_tree.no_lineage", default="No known lineage data for this cat yet."), styleSheet="color:#666; font-size:12px;"))

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
        self._cache: Optional[BreedingCache] = None

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        left = QWidget()
        left.setFixedWidth(320)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        self._list_title = QLabel(styleSheet="color:#666; font-size:10px; font-weight:bold;")
        lv.addWidget(self._list_title)
        self._search = QLineEdit()
        lv.addWidget(self._search)
        self._list = QListWidget()
        lv.addWidget(self._list, 1)
        root.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        self._title = QLabel()
        self._title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        self._table = QTableWidget(0, 4)
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
        self.retranslate_ui()
        _enforce_min_font_in_widget_tree(self)

    def retranslate_ui(self):
        self._list_title.setText(_tr("safe_breeding.list_title"))
        self._search.setPlaceholderText(_tr("safe_breeding.search_placeholder"))
        self._table.setHorizontalHeaderLabels([
            _tr("safe_breeding.table.cat"),
            _tr("safe_breeding.table.risk"),
            _tr("safe_breeding.table.shared_ancestors"),
            _tr("safe_breeding.table.child_outcome"),
        ])
        self._refresh_list()

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

    def set_cache(self, cache: Optional['BreedingCache']):
        self._cache = cache
        # Re-render the currently selected cat with cached data
        cur = self._list.currentItem()
        if cur is not None:
            self._render_for(self._by_key.get(int(cur.data(Qt.UserRole))))

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
            text = f"{cat.name}  ({cat.gender_display})"
            if cat.is_blacklisted:
                text += f"  [{_tr('safe_breeding.list.blocked')}]"
            if cat.must_breed:
                text += f"  [{_tr('safe_breeding.list.must')}]"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, cat.db_key)
            if cat.is_blacklisted:
                item.setForeground(QBrush(QColor(170, 100, 100)))
            if cat.must_breed:
                item.setForeground(QBrush(QColor(98, 194, 135)))
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
            self._title.setText(_tr("safe_breeding.title"))
            self._summary.setText(_tr("safe_breeding.summary_empty"))
            return

        cache = self._cache
        self._title.setText(_tr("safe_breeding.title_with_cat", name=cat.name))
        candidates: list[tuple[float, int, int, Cat]] = []
        for other in self._alive:
            if other is cat:
                continue
            ok, _ = can_breed(cat, other)
            if not ok:
                continue
            if cache is not None and cache.ready:
                shared, recent_shared = cache.get_shared(cat, other, recent_depth=3)
                rel = cache.get_risk(cat, other)
            else:
                shared, recent_shared = shared_ancestor_counts(cat, other, recent_depth=3)
                rel = risk_percent(cat, other)
            closest_recent_gen = 0
            if recent_shared:
                if cache is not None and cache.ready:
                    da = cache.get_ancestor_depths_for(cat)
                    db = cache.get_ancestor_depths_for(other)
                else:
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

        self._summary.setText(_tr("safe_breeding.summary", count=len(candidates)))
        self._table.setRowCount(len(candidates))
        for row, (rel, packed_shared, closest_recent_gen, other) in enumerate(candidates):
            self._table_row_cat_keys.append(other.db_key)
            shared = packed_shared % 1000
            risk_pct = int(round(rel))
            if risk_pct >= 100:
                tag, col = _tr("safe_breeding.tag.highly_inbred"), QColor(217, 119, 119)
            elif risk_pct >= 50:
                tag, col = _tr("safe_breeding.tag.moderately_inbred"), QColor(216, 181, 106)
            elif risk_pct >= 20:
                tag, col = _tr("safe_breeding.tag.slightly_inbred"), QColor(143, 201, 230)
            else:
                tag, col = _tr("safe_breeding.tag.not_inbred"), QColor(98, 194, 135)

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


class BreedingPartnersView(QWidget):
    """Dedicated view for mutual-lover breeding pairs and room mismatches."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; }"
            "QHeaderView::section { background:#151532; color:#7d8bb0; border:none; padding:4px; font-weight:bold; }"
        )
        self._cats: list[Cat] = []
        self._pairs: list[dict[str, object]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        self._title = QLabel()
        self._title.setStyleSheet("color:#ddd; font-size:18px; font-weight:bold;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(self._title)
        header.addStretch()
        header.addWidget(self._summary)
        root.addLayout(header)

        self._search = QLineEdit()
        root.addWidget(self._search)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            _tr("breeding_partners.table.cat_a"),
            _tr("breeding_partners.table.cat_b"),
            _tr("breeding_partners.table.room_a"),
            _tr("breeding_partners.table.room_b"),
            _tr("breeding_partners.table.status"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(False)
        hh = self._table.horizontalHeader()
        for col in range(4):
            hh.setSectionResizeMode(col, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        self._table.setColumnWidth(4, 120)
        root.addWidget(self._table, 1)

        self._search.textChanged.connect(self._refresh_table)
        self.retranslate_ui()
        _enforce_min_font_in_widget_tree(self)
        self.retranslate_ui()

    def retranslate_ui(self):
        self._title.setText(_tr("breeding_partners.title", default="Breeding Partners"))
        self._search.setPlaceholderText(_tr("breeding_partners.search_placeholder", default="Search partner names or rooms…"))
        self._table.setHorizontalHeaderLabels([
            _tr("breeding_partners.table.cat_a", default="Cat A"),
            _tr("breeding_partners.table.cat_b", default="Cat B"),
            _tr("breeding_partners.table.room_a", default="Room A"),
            _tr("breeding_partners.table.room_b", default="Room B"),
            _tr("breeding_partners.table.status", default="Status"),
        ])
        self._refresh_table()

    def set_cats(self, cats: list[Cat]):
        self._cats = cats
        self._pairs = []
        seen: set[tuple[int, int]] = set()
        alive = [c for c in cats if c.status != "Gone"]
        for cat in alive:
            for lover in cat.lovers:
                if lover.status == "Gone":
                    continue
                if cat not in lover.lovers:
                    continue
                key = (cat.db_key, lover.db_key) if cat.db_key < lover.db_key else (lover.db_key, cat.db_key)
                if key in seen:
                    continue
                seen.add(key)
                same_room = bool(cat.room and lover.room and cat.room == lover.room and cat.status == lover.status == "In House")
                self._pairs.append({
                    "cat_a": cat,
                    "cat_b": lover,
                    "room_a": cat.room_display or cat.status,
                    "room_b": lover.room_display or lover.status,
                    "same_room": same_room,
                })
        self._pairs.sort(key=lambda p: (
            not bool(p["same_room"]),
            str(p["cat_a"].name).lower(),
            str(p["cat_b"].name).lower(),
        ))
        self._refresh_table()

    def _refresh_table(self):
        query = self._search.text().strip().lower()
        pairs = self._pairs
        if query:
            pairs = [
                p for p in pairs
                if query in " ".join([
                    p["cat_a"].name.lower(),
                    p["cat_b"].name.lower(),
                    str(p["room_a"]).lower(),
                    str(p["room_b"]).lower(),
                ])
            ]

        self._table.setRowCount(len(pairs))
        mismatch_count = 0
        for row, pair in enumerate(pairs):
            same_room = bool(pair["same_room"])
            if not same_room:
                mismatch_count += 1
            status_text = _tr("breeding_partners.status.same_room") if same_room else _tr("breeding_partners.status.mismatch")
            status_color = QColor(98, 194, 135) if same_room else QColor(216, 181, 106)
            items = [
                QTableWidgetItem(f"{pair['cat_a'].name} ({pair['cat_a'].gender_display})"),
                QTableWidgetItem(f"{pair['cat_b'].name} ({pair['cat_b'].gender_display})"),
                QTableWidgetItem(str(pair["room_a"])),
                QTableWidgetItem(str(pair["room_b"])),
                QTableWidgetItem(status_text),
            ]
            items[4].setTextAlignment(Qt.AlignCenter)
            items[4].setForeground(QBrush(status_color))
            if not same_room:
                for item in items[:4]:
                    item.setBackground(QBrush(QColor(48, 36, 14)))
            for col, item in enumerate(items):
                self._table.setItem(row, col, item)

        total = len(self._pairs)
        shown = len(pairs)
        self._summary.setText(_tr("breeding_partners.summary",
                                   shown=shown, total=total, mismatches=mismatch_count))

    def retranslate_ui(self):
        self._title.setText(_tr("breeding_partners.title"))
        self._search.setPlaceholderText(_tr("breeding_partners.search_placeholder"))
        self._table.setHorizontalHeaderLabels([
            _tr("breeding_partners.table.cat_a"),
            _tr("breeding_partners.table.cat_b"),
            _tr("breeding_partners.table.room_a"),
            _tr("breeding_partners.table.room_b"),
            _tr("breeding_partners.table.status"),
        ])


# ── Room Optimizer View ───────────────────────────────────────────────────────

class RoomOptimizerView(QWidget):
    """View for optimizing cat room distribution to maximize breeding outcomes."""

    @staticmethod
    def _set_toggle_button_label(btn: QPushButton, label_key: str, default_label: str):
        _set_localized_toggle_button_label(btn, label_key, default_label)

    @staticmethod
    def _bind_persistent_toggle(btn: QPushButton, label_key: str, key: str, default_label: str):
        RoomOptimizerView._set_toggle_button_label(btn, label_key, default_label)
        btn.toggled.connect(lambda checked: _set_optimizer_flag(key, checked))
        btn.toggled.connect(lambda _: RoomOptimizerView._set_toggle_button_label(btn, label_key, default_label))

    def _set_mode_button_text(self, enabled: bool):
        key = "room_optimizer.mode_family" if enabled else "room_optimizer.mode_pair"
        self._mode_toggle_btn.setText(_tr(key))
        self._mode_toggle_btn.setToolTip(_tr("room_optimizer.mode_tooltip"))

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
        self._cache: Optional[BreedingCache] = None
        self._optimizer_worker: Optional[RoomOptimizerWorker] = None
        self._planner_view: Optional['MutationDisorderPlannerView'] = None
        self._planner_traits: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        self._title = QLabel()
        self._title.setStyleSheet("color:#ddd; font-size:18px; font-weight:bold;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        self._summary.setWordWrap(True)
        self._summary.setMaximumHeight(50)
        self._summary.setAlignment(Qt.AlignRight | Qt.AlignTop)
        header.addWidget(self._title)
        header.addWidget(self._summary, 1)  # stretch=1 to fill space
        root.addLayout(header)

        # Controls
        controls_wrap = QScrollArea()
        controls_wrap.setWidgetResizable(True)
        controls_wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        controls_wrap.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_wrap.setFrameShape(QFrame.NoFrame)
        controls_wrap.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        controls_box = QWidget()
        controls = QHBoxLayout(controls_box)
        controls.setSpacing(8)
        controls.setContentsMargins(0, 0, 0, 0)

        self._min_stats_label = QLabel()
        self._min_stats_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._min_stats_label)

        self._min_stats_input = QLineEdit()
        self._min_stats_input.setPlaceholderText("")
        self._min_stats_input.setFixedWidth(60)
        self._min_stats_input.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        controls.addWidget(self._min_stats_input)

        controls.addSpacing(16)

        self._max_risk_label = QLabel()
        self._max_risk_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._max_risk_label)

        self._max_risk_input = QLineEdit()
        self._max_risk_input.setPlaceholderText("")
        self._max_risk_input.setFixedWidth(60)
        self._max_risk_input.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        controls.addWidget(self._max_risk_input)

        controls.addSpacing(16)

        self._optimize_btn = QPushButton()
        self._optimize_btn.clicked.connect(self._calculate_optimal_distribution)
        self._optimize_btn.setStyleSheet(
            "QPushButton { background:#1f5f4a; color:#f2f7f3; border:1px solid #3f8f72; "
            "border-radius:4px; padding:6px 14px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#26735a; }"
            "QPushButton:pressed { background:#184b3a; }"
        )
        controls.addWidget(self._optimize_btn)

        controls.addSpacing(8)

        self._mode_toggle_btn = QPushButton()
        self._mode_toggle_btn.setCheckable(True)
        self._mode_toggle_btn.setChecked(False)
        self._mode_toggle_btn.setToolTip("")
        self._mode_toggle_btn.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#3a2f54; color:#ddd; border:1px solid #6a5a9a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._mode_toggle_btn.toggled.connect(self._on_optimizer_mode_toggled)
        controls.addWidget(self._mode_toggle_btn)

        controls.addSpacing(8)

        self._minimize_variance_checkbox = QPushButton()
        self._minimize_variance_checkbox.setCheckable(True)
        self._minimize_variance_checkbox.setChecked(_saved_optimizer_flag("minimize_variance", True))
        self._minimize_variance_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#2a4a5a; color:#ddd; border:1px solid #4a6a7a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(self._minimize_variance_checkbox, "room_optimizer.toggle.minimize_variance", "minimize_variance", "Minimize Variance")
        controls.addWidget(self._minimize_variance_checkbox)

        self._avoid_lovers_checkbox = QPushButton()
        self._avoid_lovers_checkbox.setCheckable(True)
        self._avoid_lovers_checkbox.setChecked(_saved_optimizer_flag("avoid_lovers", True))
        self._avoid_lovers_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#5a3a2a; color:#ddd; border:1px solid #8a5a4a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(self._avoid_lovers_checkbox, "room_optimizer.toggle.avoid_lovers", "avoid_lovers", "Avoid Lovers")
        controls.addWidget(self._avoid_lovers_checkbox)

        self._prefer_low_aggression_checkbox = QPushButton()
        self._prefer_low_aggression_checkbox.setCheckable(True)
        self._prefer_low_aggression_checkbox.setChecked(_saved_optimizer_flag("prefer_low_aggression", True))
        self._prefer_low_aggression_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#4a2a2a; color:#ddd; border:1px solid #7a4a4a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(self._prefer_low_aggression_checkbox, "room_optimizer.toggle.prefer_low_aggression", "prefer_low_aggression", "Prefer Low Aggression")
        controls.addWidget(self._prefer_low_aggression_checkbox)

        self._prefer_high_libido_checkbox = QPushButton()
        self._prefer_high_libido_checkbox.setCheckable(True)
        self._prefer_high_libido_checkbox.setChecked(_saved_optimizer_flag("prefer_high_libido", True))
        self._prefer_high_libido_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#2a4a36; color:#ddd; border:1px solid #4a7a5a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(self._prefer_high_libido_checkbox, "room_optimizer.toggle.prefer_high_libido", "prefer_high_libido", "Prefer High Libido")
        controls.addWidget(self._prefer_high_libido_checkbox)

        controls.addSpacing(16)
        self._import_planner_btn = QPushButton()
        self._import_planner_btn.setToolTip("")
        self._import_planner_btn.setStyleSheet(
            "QPushButton { background:#2a2a5a; color:#bbbbee; border:1px solid #4a4a8a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:hover { background:#3a3a6a; color:#ddd; }"
        )
        self._import_planner_btn.clicked.connect(self._import_from_planner)
        controls.addWidget(self._import_planner_btn)

        controls.addStretch()
        controls_wrap.setWidget(controls_box)
        root.addWidget(controls_wrap)

        # Splitter to hold table and details pane
        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setStyleSheet("QSplitter::handle:vertical { background:#1e1e38; }")
        
        # Results table
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            _tr("room_optimizer.table.room"),
            _tr("room_optimizer.table.cats"),
            _tr("room_optimizer.table.expected_pairs"),
            _tr("room_optimizer.table.avg_stats"),
            _tr("room_optimizer.table.risk"),
            _tr("room_optimizer.table.details"),
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
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)

        self._splitter.addWidget(self._table)

        # Bottom tabs: Cat Locator, Breeding Pairs, Excluded
        self._bottom_tabs = QTabWidget()
        self._bottom_tabs.setStyleSheet(
            "QTabWidget::pane { border:1px solid #1e1e38; background:#0a0a18; }"
            "QTabBar::tab { background:#14142a; color:#888; padding:6px 14px; border:1px solid #1e1e38;"
            " border-bottom:none; margin-right:2px; font-size:11px; }"
            "QTabBar::tab:selected { background:#1a1a36; color:#ddd; font-weight:bold; }"
            "QTabBar::tab:hover { background:#1e1e3a; color:#bbb; }"
        )

        # Tab 1: Breeding Pairs (existing detail panel)
        self._details_pane = RoomOptimizerDetailPanel()
        self._details_pane._navigate_to_cat_callback = self._navigate_to_cat_from_breeding_pairs
        self._bottom_tabs.addTab(self._details_pane, "")

        # Tab 2: Cat Locator
        self._cat_locator = RoomOptimizerCatLocator()
        self._bottom_tabs.addTab(self._cat_locator, "")

        self._splitter.addWidget(self._bottom_tabs)
        self._splitter.setSizes([180, 420])

        root.addWidget(self._splitter, 1)

        self.retranslate_ui()
        _enforce_min_font_in_widget_tree(self)
        self.retranslate_ui()

    def retranslate_ui(self):
        self._title.setText(_tr("room_optimizer.title", default="Room Distribution Optimizer"))
        self._min_stats_label.setText(_tr("room_optimizer.min_base_stats", default="Min base stats:"))
        self._max_risk_label.setText(_tr("room_optimizer.max_inbreeding_risk", default="Max inbreeding risk %:"))
        self._optimize_btn.setText(_tr("room_optimizer.calculate", default="Calculate Optimal Distribution"))
        self._mode_toggle_btn.setToolTip(_tr(
            "room_optimizer.mode.tooltip",
            default="Toggle optimizer mode:\nPair Quality = best pair scoring\nFamily Separation = spread family lines across rooms",
        ))
        self._avoid_lovers_checkbox.setToolTip(_tr(
            "room_optimizer.toggle.avoid_lovers.tooltip",
            default="If enabled, cats that already have lovers will not be paired with other cats.",
        ))
        self._prefer_low_aggression_checkbox.setToolTip(_tr(
            "room_optimizer.toggle.prefer_low_aggression.tooltip",
            default="If enabled, optimizer gives extra weight to lower-aggression cats.",
        ))
        self._prefer_high_libido_checkbox.setToolTip(_tr(
            "room_optimizer.toggle.prefer_high_libido.tooltip",
            default="If enabled, optimizer gives extra weight to higher-libido cats.",
        ))
        self._table.setHorizontalHeaderLabels([
            _tr("table.column.room", default="Room"),
            _tr("room_optimizer.table.cats_to_place", default="Cats to Place"),
            _tr("room_optimizer.table.expected_pairs", default="Expected Pairs"),
            _tr("room_optimizer.table.avg_stats", default="Avg Stats"),
            _tr("table.column.risk", default="Risk%"),
            _tr("common.details", default="Details"),
        ])
        self._bottom_tabs.setTabText(self._bottom_tabs.indexOf(self._details_pane), _tr("room_optimizer.tab.breeding_pairs", default="Breeding Pairs"))
        self._bottom_tabs.setTabText(self._bottom_tabs.indexOf(self._cat_locator), _tr("room_optimizer.tab.cat_locator", default="Cat Locator"))
        self._details_pane.retranslate_ui()
        self._cat_locator.retranslate_ui()
        self._on_optimizer_mode_toggled(self._mode_toggle_btn.isChecked())
        if not self._summary.text():
            self._summary.setText(_tr("room_optimizer.summary.idle", default="Run the optimizer to see cat placements."))

    def _on_optimizer_mode_toggled(self, enabled: bool):
        self._set_mode_button_text(enabled)
        self._minimize_variance_checkbox.setChecked(False if enabled else _saved_optimizer_flag("minimize_variance", True))
        self._minimize_variance_checkbox.setEnabled(not enabled)
        self._minimize_variance_checkbox.setToolTip("" if not enabled else _tr("room_optimizer.tooltip.variance"))

    def _on_table_selection_changed(self):
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            self._details_pane.show_room(None)
            return

        row = selected_ranges[0].topRow()
        room_item = self._table.item(row, 0)
        if room_item:
            details_data = room_item.data(Qt.UserRole)
            self._details_pane.show_room(details_data if isinstance(details_data, dict) else None)

    def set_cats(self, cats: list[Cat], excluded_keys: set[int] = None):
        self._cats = cats
        # Combine explicit excluded_keys with blacklisted cats
        blacklisted_keys = {c.db_key for c in cats if c.is_blacklisted}
        self._excluded_keys = (excluded_keys or set()) | blacklisted_keys
        alive_count = len([c for c in cats if c.status != 'Gone'])
        excluded_count = len([c for c in cats if c.status != 'Gone' and c.db_key in self._excluded_keys])
        if excluded_count > 0:
            self._summary.setText(_tr("room_optimizer.summary.with_excluded",
                                       alive=alive_count, excluded=excluded_count))
        else:
            self._summary.setText(_tr("room_optimizer.summary.no_excluded",
                                       alive=alive_count))

    def _navigate_to_cat_from_breeding_pairs(self, cat_name_formatted: str):
        """Navigate to a cat by its formatted name (e.g. 'Fluffy (Female)')."""
        # Extract the cat name part (before the gender)
        cat_name = cat_name_formatted.split(" (")[0] if " (" in cat_name_formatted else cat_name_formatted

        # Find the cat by name
        for cat in self._cats:
            if cat.name == cat_name:
                # Call the cat locator's callback if available
                if self._cat_locator._navigate_to_cat_callback:
                    self._cat_locator._navigate_to_cat_callback(cat.db_key)
                return

    def set_cache(self, cache: Optional['BreedingCache']):
        self._cache = cache

    def set_planner_view(self, planner: 'MutationDisorderPlannerView'):
        self._planner_view = planner

    def _import_from_planner(self):
        if self._planner_view is None:
            return
        self._planner_traits = self._planner_view.get_selected_traits()
        if not self._planner_traits:
            self._import_planner_btn.setText(_tr("room_optimizer.import_planner"))
            self._import_planner_btn.setToolTip(_tr("room_optimizer.import_none_tooltip"))
            return
        names = [f"{t['display'].split('] ')[-1]}({t['weight']})" for t in self._planner_traits[:4]]
        summary = ", ".join(names)
        if len(self._planner_traits) > 4:
            summary += f" +{len(self._planner_traits) - 4} more"
        self._import_planner_btn.setText(_tr("room_optimizer.imported", summary=summary))
        self._import_planner_btn.setStyleSheet(
            "QPushButton { background:#2a3a5a; color:#aaddff; border:1px solid #4a6a9a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:hover { background:#3a4a6a; color:#ddd; }"
        )

    def retranslate_ui(self):
        self._title.setText(_tr("room_optimizer.title"))
        self._summary.setText(_tr("room_optimizer.summary_empty"))
        self._min_stats_label.setText(_tr("room_optimizer.min_stats"))
        self._min_stats_input.setPlaceholderText(_tr("room_optimizer.placeholder.min_stats"))
        self._max_risk_label.setText(_tr("room_optimizer.max_risk"))
        self._max_risk_input.setPlaceholderText(_tr("room_optimizer.placeholder.max_risk"))
        self._optimize_btn.setText(_tr("room_optimizer.optimize_btn"))
        self._set_mode_button_text(self._mode_toggle_btn.isChecked())
        self._import_planner_btn.setText(_tr("room_optimizer.import_planner"))
        self._import_planner_btn.setToolTip(_tr("room_optimizer.import_none_tooltip"))
        self._table.setHorizontalHeaderLabels([
            _tr("room_optimizer.table.room"),
            _tr("room_optimizer.table.cats"),
            _tr("room_optimizer.table.expected_pairs"),
            _tr("room_optimizer.table.avg_stats"),
            _tr("room_optimizer.table.risk"),
            _tr("room_optimizer.table.details"),
        ])

    def _calculate_optimal_distribution(self):
        """Kick off background optimizer worker."""
        if self._optimizer_worker is not None and self._optimizer_worker.isRunning():
            return  # already running

        min_stats = 0
        try:
            if self._min_stats_input.text().strip():
                min_stats = int(self._min_stats_input.text().strip())
        except ValueError:
            pass

        max_risk = 10.0
        try:
            if self._max_risk_input.text().strip():
                max_risk = float(self._max_risk_input.text().strip())
        except ValueError:
            pass

        params = {
            "min_stats": min_stats,
            "max_risk": max_risk,
            "minimize_variance": self._minimize_variance_checkbox.isChecked(),
            "avoid_lovers": self._avoid_lovers_checkbox.isChecked(),
            "prefer_low_aggression": self._prefer_low_aggression_checkbox.isChecked(),
            "prefer_high_libido": self._prefer_high_libido_checkbox.isChecked(),
            "mode_family": self._mode_toggle_btn.isChecked(),
            "planner_traits": list(self._planner_traits),
        }

        self._optimize_btn.setEnabled(False)
        self._summary.setText(_tr("room_optimizer.status.calculating"))

        worker = RoomOptimizerWorker(
            self._cats,
            getattr(self, "_excluded_keys", set()),
            self._cache,
            params,
            parent=self,
        )
        worker.finished.connect(self._on_optimizer_result)
        self._optimizer_worker = worker
        worker.start()

    def _on_optimizer_result(self, result: dict):
        self._optimizer_worker = None
        self._optimize_btn.setEnabled(True)

        if "error" in result:
            self._table.setRowCount(0)
            self._summary.setText(_tr("room_optimizer.status.error", message=result["error"]))
            return

        room_rows = result["room_rows"]
        locator_data = result["locator_data"]
        excluded_rows = result["excluded_rows"]
        mode_family = result["mode_family"]
        min_stats = result["min_stats"]
        max_risk = result["max_risk"]
        minimize_variance = result["minimize_variance"]
        avoid_lovers = result["avoid_lovers"]
        prefer_low_aggression = result["prefer_low_aggression"]
        prefer_high_libido = result["prefer_high_libido"]

        self._cat_locator.show_assignments(locator_data)
        self._table.setRowCount(0)
        self._details_pane.show_room(None)

        row_idx = 0
        total_pairs = 0
        total_assigned = 0

        for room_data in room_rows:
            room_label = room_data["room_label"]
            cat_names = room_data["cat_names"]
            room_pairs = room_data["pairs"]
            avg_stats = room_data["avg_stats"]
            avg_risk = room_data["avg_risk"]
            is_fallback = room_data["is_fallback"]

            total_assigned += len(cat_names)
            total_pairs += len(room_pairs)

            self._table.insertRow(row_idx)

            room_item = QTableWidgetItem(room_label)
            room_item.setTextAlignment(Qt.AlignCenter)
            if is_fallback:
                room_item.setForeground(QBrush(QColor(150, 150, 150)))

            cats_item = QTableWidgetItem(", ".join(cat_names))

            pairs_item = QTableWidgetItem(str(len(room_pairs)))
            pairs_item.setTextAlignment(Qt.AlignCenter)

            stats_item = QTableWidgetItem(f"{avg_stats:.1f}")
            stats_item.setTextAlignment(Qt.AlignCenter)
            if avg_stats >= 200:
                stats_item.setForeground(QBrush(QColor(98, 194, 135)))
            elif avg_stats >= 150:
                stats_item.setForeground(QBrush(QColor(143, 201, 230)))
            else:
                stats_item.setForeground(QBrush(QColor(190, 145, 40)))

            risk_item = QTableWidgetItem(f"{avg_risk:.0f}%")
            risk_item.setTextAlignment(Qt.AlignCenter)
            if avg_risk >= 50:
                risk_item.setForeground(QBrush(QColor(217, 119, 119)))
            elif avg_risk >= 20:
                risk_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                risk_item.setForeground(QBrush(QColor(98, 194, 135)))

            details_lines = []
            for p in room_pairs[:3]:
                details_lines.append(
                    _tr(
                        "room_optimizer.result.detail_line",
                        default="{cat_a} × {cat_b} (stats: {stats:.0f}, risk: {risk:.0f}%)",
                        cat_a=p["cat_a"],
                        cat_b=p["cat_b"],
                        stats=p["avg_stats"],
                        risk=p["risk"],
                    )
                )
            if len(room_pairs) > 3:
                details_lines.append(
                    _tr("room_optimizer.result.more_pairs", default="... and {count} more", count=len(room_pairs) - 3)
                )
            details_item = QTableWidgetItem("; ".join(details_lines))

            room_item.setData(Qt.UserRole, {
                "room": room_label,
                "cats": cat_names,
                "total_pairs": len(room_pairs),
                "avg_stats": avg_stats,
                "avg_risk": avg_risk,
                "excluded_cats": [],
                "pairs": room_pairs,
            })

            self._table.setItem(row_idx, 0, room_item)
            self._table.setItem(row_idx, 1, cats_item)
            self._table.setItem(row_idx, 2, pairs_item)
            self._table.setItem(row_idx, 3, stats_item)
            self._table.setItem(row_idx, 4, risk_item)
            self._table.setItem(row_idx, 5, details_item)
            row_idx += 1

        if excluded_rows:
            excluded_names = [r["name"] for r in excluded_rows]
            self._table.insertRow(row_idx)
            excluded_room_item = QTableWidgetItem(_tr("room_optimizer.row.excluded", default="Excluded"))
            excluded_room_item.setTextAlignment(Qt.AlignCenter)
            excluded_room_item.setForeground(QBrush(QColor(170, 120, 120)))
            excluded_room_item.setData(Qt.UserRole, {
                "room": _tr("room_optimizer.row.excluded", default="Excluded"),
                "cats": excluded_names,
                "total_pairs": 0,
                "avg_stats": 0.0,
                "avg_risk": 0.0,
                "excluded_cats": excluded_names,
                "excluded_cat_rows": excluded_rows,
                "pairs": [],
            })
            self._table.setItem(row_idx, 0, excluded_room_item)
            self._table.setItem(
                row_idx,
                1,
                QTableWidgetItem(_tr("room_optimizer.row.excluded_cats", default="{count} excluded cats", count=len(excluded_rows))),
            )
            for col in (2, 3, 4):
                dash = QTableWidgetItem("—")
                dash.setTextAlignment(Qt.AlignCenter)
                self._table.setItem(row_idx, col, dash)
            self._table.setItem(row_idx, 5, QTableWidgetItem(_tr("room_optimizer.row.excluded_details", default="Excluded from optimizer breeding calculations")))
            row_idx += 1

        filter_info = [
            _tr(
                "room_optimizer.filter.mode",
                default="mode: {mode}",
                mode=_tr(
                    "room_optimizer.filter.mode.family_separation",
                    default="family separation",
                ) if mode_family else _tr("room_optimizer.filter.mode.pair_quality", default="pair quality"),
            )
        ]
        if min_stats > 0:
            filter_info.append(_tr("room_optimizer.filter.min_stats", default="min stats: {value}", value=min_stats))
        if max_risk < 100:
            filter_info.append(_tr("room_optimizer.filter.max_risk", default="max risk: {value}%", value=max_risk))
        if (not mode_family) and minimize_variance:
            filter_info.append(_tr("room_optimizer.filter.variance_on", default="variance: on"))
        if prefer_low_aggression:
            filter_info.append(_tr("room_optimizer.filter.prefer_low_aggression", default="prefer low aggression"))
        if prefer_high_libido:
            filter_info.append(_tr("room_optimizer.filter.prefer_high_libido", default="prefer high libido"))
        if avoid_lovers:
            filter_info.append(_tr("room_optimizer.filter.avoid_lovers", default="avoid lovers"))
        filter_str = (
            _tr("room_optimizer.filter.summary", default="  |  Filters: {filters}", filters=", ".join(filter_info))
            if filter_info else ""
        )

        self._summary.setText(
            _tr(
                "room_optimizer.summary.optimized",
                default="Optimized {total_assigned} cats into {room_count} rooms  |  {total_pairs} total breeding pairs{filter_str}",
                total_assigned=total_assigned,
                room_count=row_idx,
                total_pairs=total_pairs,
                filter_str=filter_str,
            )
        )


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
        from types import SimpleNamespace
        p = self._params
        cache = self._cache

        excluded_keys = self._excluded_keys
        alive_cats = [c for c in self._cats if c.status != "Gone" and c.db_key not in excluded_keys]
        excluded_cats = [c for c in self._cats if c.status != "Gone" and c.db_key in excluded_keys]

        min_stats = p["min_stats"]
        max_risk = p["max_risk"]
        minimize_variance = p["minimize_variance"]
        avoid_lovers = p["avoid_lovers"]
        prefer_low_aggression = p["prefer_low_aggression"]
        prefer_high_libido = p["prefer_high_libido"]
        mode_family = p["mode_family"]

        if min_stats > 0:
            alive_cats = [c for c in alive_cats if sum(c.base_stats.values()) >= min_stats]

        if len(alive_cats) < 2:
            self.finished.emit({"error": _tr("room_optimizer.error.not_enough_cats", default="Not enough cats to optimize")})
            return

        stat_sum = {cat.db_key: sum(cat.base_stats.values()) for cat in alive_cats}

        pair_eval_cache: dict[tuple[int, int], tuple[bool, str, float]] = {}
        hater_key_map = {cat.db_key: {o.db_key for o in getattr(cat, "haters", [])} for cat in alive_cats}
        lover_key_map = {cat.db_key: {o.db_key for o in getattr(cat, "lovers", [])} for cat in alive_cats}

        def _pair_key(a, b):
            ak, bk = a.db_key, b.db_key
            return (ak, bk) if ak < bk else (bk, ak)

        def _is_hater_conflict(a, b):
            return b.db_key in hater_key_map.get(a.db_key, set()) or a.db_key in hater_key_map.get(b.db_key, set())

        def _is_mutual_lover_pair(a, b):
            return b.db_key in lover_key_map.get(a.db_key, set()) and a.db_key in lover_key_map.get(b.db_key, set())

        def _trait_or_default(v, default=0.5):
            return default if v is None else max(0.0, min(1.0, float(v)))

        def _personality_score(a, b=None):
            cats = [a] if b is None else [a, b]
            score = 0.0
            if prefer_low_aggression:
                score += sum(1.0 - _trait_or_default(c.aggression) for c in cats) / len(cats)
            if prefer_high_libido:
                score += sum(_trait_or_default(c.libido) for c in cats) / len(cats)
            return score

        def _is_lover_conflict(a, b):
            if not avoid_lovers:
                return False
            la = lover_key_map.get(a.db_key, set())
            lb = lover_key_map.get(b.db_key, set())
            return (la and b.db_key not in la) or (lb and a.db_key not in lb)

        def _pair_eval(a, b):
            key = _pair_key(a, b)
            cached = pair_eval_cache.get(key)
            if cached is not None:
                return cached
            ok, reason = can_breed(a, b)
            if ok and _is_hater_conflict(a, b):
                ok, reason = False, "These cats hate each other"
            if ok and _is_lover_conflict(a, b):
                ok, reason = False, "One or both cats already have a lover"
            if ok:
                if cache is not None and cache.ready:
                    risk = cache.risk_pct.get(cache._pair_key(a.db_key, b.db_key), 0.0)
                else:
                    risk = risk_percent(a, b)
            else:
                risk = 0.0
            pair_eval_cache[key] = (ok, reason, risk)
            return pair_eval_cache[key]

        def _room_conflict(a, b):
            if _is_hater_conflict(a, b) or _is_lover_conflict(a, b):
                return True
            ok, _, risk = _pair_eval(a, b)
            return ok and risk > max_risk

        # ── Planner trait bonus ──
        planner_traits = p.get("planner_traits", [])

        males   = sorted([c for c in alive_cats if c.gender == "male"],   key=lambda c: stat_sum[c.db_key], reverse=True)
        females = sorted([c for c in alive_cats if c.gender == "female"], key=lambda c: stat_sum[c.db_key], reverse=True)
        unknown = sorted([c for c in alive_cats if c.gender == "?"],      key=lambda c: stat_sum[c.db_key], reverse=True)
        all_cats = males + females + unknown

        if mode_family:
            all_rooms = list(ROOM_DISPLAY.keys())
            fallback_room = None
            max_cats_per_room = 6
            family_assignments = {room: {"males": [], "females": [], "unknown": []} for room in all_rooms}

            def _room_cats(room_key):
                rd = family_assignments[room_key]
                return rd["males"] + rd["females"] + rd["unknown"]

            def _preferred_rooms(cat):
                if avoid_lovers:
                    return list(all_rooms)
                lover_rooms = [r for r in all_rooms if any(_is_mutual_lover_pair(cat, ec) for ec in _room_cats(r))]
                return lover_rooms + [r for r in all_rooms if r not in lover_rooms]

            def _family_group_id(cat):
                ancestors = []
                for p in (cat.parent_a, cat.parent_b):
                    if p:
                        ancestors.append(p.db_key)
                        for gp in (p.parent_a, p.parent_b):
                            if gp:
                                ancestors.append(gp.db_key)
                return tuple(sorted(ancestors)) if ancestors else None

            for gender_list, gender_key in ((males, "males"), (females, "females"), (unknown, "unknown")):
                family_groups: dict = {}
                no_family = []
                for cat in gender_list:
                    fid = _family_group_id(cat)
                    (family_groups.setdefault(fid, []) if fid else no_family).append(cat)

                for fid, fcats in family_groups.items():
                    for cat in fcats:
                        placed = False
                        for room in _preferred_rooms(cat):
                            rc = _room_cats(room)
                            if len(rc) >= max_cats_per_room:
                                continue
                            if any(_family_group_id(ec) == fid or _room_conflict(cat, ec) for ec in rc):
                                continue
                            family_assignments[room][gender_key].append(cat)
                            placed = True
                            break
                        if not placed:
                            best_room = min(
                                (r for r in _preferred_rooms(cat) if len(_room_cats(r)) < max_cats_per_room),
                                key=lambda r: sum(_pair_eval(cat, ec)[2] for ec in _room_cats(r) if not _is_hater_conflict(cat, ec)),
                                default=min(all_rooms, key=lambda r: len(_room_cats(r))),
                            )
                            family_assignments[best_room][gender_key].append(cat)

                for cat in no_family:
                    placed = False
                    for room in _preferred_rooms(cat):
                        rc = _room_cats(room)
                        if len(rc) < max_cats_per_room and not any(_room_conflict(cat, ec) for ec in rc):
                            family_assignments[room][gender_key].append(cat)
                            placed = True
                            break
                    if not placed:
                        best_room = min(
                            (r for r in _preferred_rooms(cat) if len(_room_cats(r)) < max_cats_per_room),
                            key=lambda r: sum(_pair_eval(cat, ec)[2] for ec in _room_cats(r) if not _is_hater_conflict(cat, ec)),
                            default=min(all_rooms, key=lambda r: len(_room_cats(r))),
                        )
                        family_assignments[best_room][gender_key].append(cat)

            room_assignments = {room: _room_cats(room) for room in all_rooms}

        else:
            # Determine number of priority rooms from actual rooms in the save.
            # Reserve one room as fallback (non-breeding overflow), rest are priority.
            actual_rooms = set()
            for c in alive_cats:
                if c.room and c.room != "Adventure" and c.status == "In House":
                    actual_rooms.add(c.room)
            n_priority = max(len(actual_rooms) - 1, 1)
            priority_rooms = [
                _tr("room_optimizer.priority_room", default="Priority {index}", index=i + 1)
                for i in range(n_priority)
            ]
            fallback_room = _tr("room_optimizer.fallback_room", default="Fallback")
            all_rooms = priority_rooms + [fallback_room]
            room_assignments = {room: [] for room in all_rooms}

            candidate_pairs = (
                [(a, b) for a in males for b in females]
                + [(a, b) for a in males for b in unknown]
                + [(a, b) for a in females for b in unknown]
                + [(unknown[i], unknown[j]) for i in range(len(unknown)) for j in range(i+1, len(unknown))]
            )

            stimulation = 50.0
            better_stat_chance = (1.0 + 0.01 * stimulation) / (2.0 + 0.01 * stimulation)
            pairs_with_scores = []
            for cat_a, cat_b in candidate_pairs:
                ok, _, risk = _pair_eval(cat_a, cat_b)
                if not ok or risk > max_risk:
                    continue
                expected_stats_sum = sum(
                    max(cat_a.base_stats[s], cat_b.base_stats[s]) * better_stat_chance
                    + min(cat_a.base_stats[s], cat_b.base_stats[s]) * (1.0 - better_stat_chance)
                    for s in STAT_NAMES
                )
                avg_base_stats = expected_stats_sum / len(STAT_NAMES)
                complementarity_bonus = sum(0.5 for s in STAT_NAMES if max(cat_a.base_stats[s], cat_b.base_stats[s]) >= 8)
                variance_penalty = sum(
                    abs(cat_a.base_stats[s] - cat_b.base_stats[s]) * 2.0
                    for s in STAT_NAMES if minimize_variance and abs(cat_a.base_stats[s] - cat_b.base_stats[s]) > 2
                )
                personality_bonus = _personality_score(cat_a, cat_b) * 2.5
                # Planner trait bonus: reward pairs that carry desired traits
                trait_bonus = 0.0
                if planner_traits:
                    for t in planner_traits:
                        a_has = _cat_has_trait(cat_a, t["category"], t["key"])
                        b_has = _cat_has_trait(cat_b, t["category"], t["key"])
                        wf = t["weight"] / 10.0
                        if a_has or b_has:
                            trait_bonus += wf * 5.0
                            if a_has and b_has:
                                trait_bonus += wf * 2.5
                quality = (avg_base_stats + complementarity_bonus) * (1.0 - risk / 200.0) - variance_penalty + personality_bonus + trait_bonus
                must_breed_bonus = 1000 if (cat_a.must_breed or cat_b.must_breed) else 0
                lover_bonus = 0.0 if avoid_lovers else (500.0 if _is_mutual_lover_pair(cat_a, cat_b) else 0.0)
                pairs_with_scores.append({
                    "cat_a": cat_a, "cat_b": cat_b, "risk": risk,
                    "avg_stats": avg_base_stats, "quality": quality,
                    "must_breed_bonus": must_breed_bonus, "lover_bonus": lover_bonus,
                })

            pairs_with_scores.sort(key=lambda p: (p["must_breed_bonus"], p["lover_bonus"], p["quality"]), reverse=True)
            assigned_cats: set[int] = set()
            max_per_room = 6

            for pair in pairs_with_scores:
                a, b = pair["cat_a"], pair["cat_b"]
                if a.db_key in assigned_cats or b.db_key in assigned_cats:
                    continue
                placed = False
                for room in priority_rooms:
                    rc = room_assignments[room]
                    if len(rc) >= max_per_room:
                        continue
                    if any(_room_conflict(a, ec) or _room_conflict(b, ec) for ec in rc):
                        continue
                    if len(rc) + 2 <= max_per_room:
                        rc.extend([a, b])
                        assigned_cats.update([a.db_key, b.db_key])
                        placed = True
                        break
                if not placed:
                    for cat in [a, b]:
                        if cat.db_key in assigned_cats:
                            continue
                        preferred = sorted(
                            priority_rooms,
                            key=lambda r: (
                                avoid_lovers or not any(_is_mutual_lover_pair(cat, ec) for ec in room_assignments[r]),
                                len(room_assignments[r]),
                            ),
                        )
                        for room in preferred:
                            rc = room_assignments[room]
                            if len(rc) < max_per_room and not any(_room_conflict(cat, ec) for ec in rc):
                                rc.append(cat)
                                assigned_cats.add(cat.db_key)
                                break

            for cat in all_cats:
                if cat.db_key not in assigned_cats:
                    room_assignments[fallback_room].append(cat)

        # Build locator data
        locator_data = []
        for room_idx, room in enumerate(all_rooms):
            assigned_room_label = ROOM_DISPLAY.get(room, room)
            for c in room_assignments[room]:
                current = c.room_display or STATUS_ABBREV.get(c.status, c.status) or _tr("common.unknown", default="?")
                needs_move = c.status != "In House" or c.room_display != assigned_room_label
                locator_data.append({
                    "name": c.name, "gender_display": c.gender_display,
                    "db_key": c.db_key,
                    "age": c.age if c.age is not None else c.db_key,
                    "current_room": current, "assigned_room": assigned_room_label,
                    "room_order": room_idx, "needs_move": needs_move,
                })

        # Build per-room display data (no Qt objects)
        room_rows = []
        for room in all_rooms:
            cats_in_room = room_assignments[room]
            if not cats_in_room:
                continue
            cat_names = [f"{c.name} ({c.gender_display})" for c in cats_in_room]
            room_pairs = []
            for i, a in enumerate(cats_in_room):
                for b in cats_in_room[i+1:]:
                    ok, _, risk = _pair_eval(a, b)
                    if ok:
                        stat_ranges = {s: (min(a.base_stats[s], b.base_stats[s]), max(a.base_stats[s], b.base_stats[s])) for s in STAT_NAMES}
                        room_pairs.append({
                            "cat_a": f"{a.name} ({a.gender_display})",
                            "cat_b": f"{b.name} ({b.gender_display})",
                            "risk": risk,
                            "avg_stats": (stat_sum[a.db_key] + stat_sum[b.db_key]) / 2,
                            "stat_ranges": stat_ranges,
                            "sum_range": (sum(lo for lo, _ in stat_ranges.values()), sum(hi for _, hi in stat_ranges.values())),
                        })
            if not room_pairs and room != fallback_room:
                continue
            room_pairs.sort(key=lambda p: (-p["avg_stats"], p["risk"]))
            avg_stats = sum(p["avg_stats"] for p in room_pairs) / len(room_pairs) if room_pairs else 0.0
            avg_risk  = sum(p["risk"]      for p in room_pairs) / len(room_pairs) if room_pairs else 0.0
            room_rows.append({
                "room": room, "room_label": ROOM_DISPLAY.get(room, room),
                "cat_names": cat_names, "pairs": room_pairs,
                "avg_stats": avg_stats, "avg_risk": avg_risk,
                "is_fallback": room == fallback_room,
            })

        excluded_rows = [
            {
                "name": f"{c.name} ({c.gender_display})",
                "stats": dict(c.base_stats), "sum": _cat_base_sum(c),
                "traits": {
                    "aggression": _trait_display_label("aggression", c.aggression) or _tr("common.unknown", default="unknown"),
                    "libido":     _trait_display_label("libido", c.libido) or _tr("common.unknown", default="unknown"),
                    "inbredness": _trait_display_label("inbredness", c.inbredness) or _tr("common.unknown", default="unknown"),
                },
            }
            for c in excluded_cats
        ]

        self.finished.emit({
            "room_rows": room_rows, "locator_data": locator_data,
            "excluded_rows": excluded_rows, "excluded_cats": excluded_cats,
            "min_stats": min_stats, "max_risk": max_risk,
            "mode_family": mode_family, "minimize_variance": minimize_variance,
            "avoid_lovers": avoid_lovers,
            "prefer_low_aggression": prefer_low_aggression,
            "prefer_high_libido": prefer_high_libido,
        })


class _SortByUserRoleItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by UserRole data instead of display text."""
    def __lt__(self, other):
        a = self.data(Qt.UserRole)
        b = other.data(Qt.UserRole) if isinstance(other, QTableWidgetItem) else None
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return super().__lt__(other)


class RoomOptimizerCatLocator(QWidget):
    """Shows all cats with their current location vs assigned room, sorted by room priority."""

    COL_CAT = 0
    COL_AGE = 1
    COL_CURRENT = 2
    COL_MOVE_TO = 3
    COL_ACTION = 4

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:#0a0a18;")
        self._navigate_to_cat_callback = None
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        self._summary = QLabel()
        self._summary.setStyleSheet("color:#888; font-size:11px;")
        root.addWidget(self._summary)

        self._table = QTableWidget(0, 5)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.setMouseTracking(True)
        self._table.cellClicked.connect(self._on_cat_clicked)
        self._table.cellEntered.connect(lambda r, c: self._table.setCursor(
            Qt.PointingHandCursor if c == self.COL_CAT else Qt.ArrowCursor
        ))
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(self.COL_CAT, QHeaderView.Fixed)
        hh.setSectionResizeMode(self.COL_AGE, QHeaderView.Fixed)
        hh.setSectionResizeMode(self.COL_CURRENT, QHeaderView.Fixed)
        hh.setSectionResizeMode(self.COL_MOVE_TO, QHeaderView.Fixed)
        hh.setSectionResizeMode(self.COL_ACTION, QHeaderView.Fixed)
        self._table.setColumnWidth(self.COL_CAT, 220)
        self._table.setColumnWidth(self.COL_AGE, 45)
        self._table.setColumnWidth(self.COL_CURRENT, 140)
        self._table.setColumnWidth(self.COL_MOVE_TO, 140)
        self._table.setColumnWidth(self.COL_ACTION, 65)
        self._table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:12px;
            }
            QTableWidget::item { padding:3px 6px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:11px; font-weight:bold;
            }
        """)
        root.addWidget(self._table, 1)
        self.retranslate_ui()

    def retranslate_ui(self):
        self._summary.setText(_tr("room_optimizer.locator.summary_idle", default="Run the optimizer to see cat placements."))
        self._table.setHorizontalHeaderLabels([
            _tr("safe_breeding.table.cat", default="Cat"),
            _tr("table.column.age", default="Age"),
            _tr("common.currently_in", default="Currently In"),
            _tr("common.move_to", default="Move To"),
            _tr("common.action", default="Action"),
        ])

    def show_assignments(self, all_assignments: list[dict]):
        """
        all_assignments: list of dicts with keys:
            name, gender_display, age, current_room, assigned_room, room_order, needs_move
        Sorted by room_order (Priority 1 first, Fallback last).
        """
        # Sort by assigned room priority, then by name within each room
        all_assignments.sort(key=lambda d: (d.get("room_order", 999), (d["name"] or "").lower()))

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(all_assignments))

        moves_needed = 0
        for row, info in enumerate(all_assignments):
            name_item = QTableWidgetItem(f"{info['name']} ({info['gender_display']})")
            name_item.setData(Qt.UserRole, info.get("db_key"))
            name_item.setForeground(QColor("#5b9bd5"))
            name_item.setToolTip(_tr("common.tooltip.jump_to_alive", default="Click to jump to this cat in Alive Cats view"))

            age_val = info.get("age")
            if isinstance(age_val, (int, float)):
                age_item = _SortByUserRoleItem(f"{age_val:.2f}" if isinstance(age_val, float) else str(age_val))
                age_item.setData(Qt.UserRole, float(age_val))
            else:
                age_item = _SortByUserRoleItem(str(age_val) if age_val is not None else "?")
                age_item.setData(Qt.UserRole, 0.0)
            age_item.setTextAlignment(Qt.AlignCenter)

            current_item = QTableWidgetItem(info["current_room"])

            assigned_item = _SortByUserRoleItem(info["assigned_room"])
            # Store room_order so sorting this column keeps room priority order
            assigned_item.setData(Qt.UserRole, info.get("room_order", 999))

            # Color row background by current room
            current_room_display = info["current_room"]
            row_bg = QColor(40, 34, 16)  # default brown
            for room_key, room_display in ROOM_DISPLAY.items():
                if room_display == current_room_display and room_key in ROOM_COLORS:
                    room_color = ROOM_COLORS[room_key]
                    row_bg = QColor(
                        max(20, room_color.red() // 3),
                        max(20, room_color.green() // 3),
                        max(20, room_color.blue() // 3)
                    )
                    break
            for it in (name_item, age_item, current_item, assigned_item):
                it.setBackground(QBrush(row_bg))

            needs_move = info.get("needs_move", False)
            if needs_move:
                moves_needed += 1
                action_item = QTableWidgetItem(_tr("common.action_move", default="MOVE"))
                action_item.setTextAlignment(Qt.AlignCenter)
                action_item.setForeground(QBrush(QColor(216, 181, 106)))
                action_item.setBackground(QBrush(row_bg))
            else:
                action_item = QTableWidgetItem(_tr("common.ok", default="OK"))
                action_item.setTextAlignment(Qt.AlignCenter)
                action_item.setForeground(QBrush(QColor(98, 194, 135)))
                action_item.setBackground(QBrush(row_bg))

            self._table.setItem(row, self.COL_CAT, name_item)
            self._table.setItem(row, self.COL_AGE, age_item)
            self._table.setItem(row, self.COL_CURRENT, current_item)
            self._table.setItem(row, self.COL_MOVE_TO, assigned_item)
            self._table.setItem(row, self.COL_ACTION, action_item)

        self._table.setSortingEnabled(True)
        # Default sort: by Move To column (room priority order)
        self._table.sortByColumn(self.COL_MOVE_TO, Qt.AscendingOrder)

        total = len(all_assignments)
        stay = total - moves_needed
        self._summary.setText(_tr(
            "room_optimizer.locator.summary",
            default="{total} cats  |  {moves_needed} need to move  |  {stay} already in place",
            total=total,
            moves_needed=moves_needed,
            stay=stay,
        ))

    def _on_cat_clicked(self, row: int, col: int):
        if col != self.COL_CAT:
            return
        item = self._table.item(row, col)
        if item is None:
            return
        db_key = item.data(Qt.UserRole)
        if db_key is not None and self._navigate_to_cat_callback is not None:
            self._navigate_to_cat_callback(db_key)

    def clear(self):
        self._table.setRowCount(0)
        self._summary.setText(_tr("room_optimizer.locator.summary_idle", default="Run the optimizer to see cat placements."))


class RoomOptimizerDetailPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:#0a0a18; border-top:1px solid #1e1e38;")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        # Header with summary label and best pairs toggle
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        self._summary = QLabel()
        self._summary.setStyleSheet("color:#aaa; font-size:12px;")
        self._summary.setWordWrap(True)
        hdr.addWidget(self._summary, 1)

        self._best_pairs_btn = QPushButton()
        self._best_pairs_btn.setCheckable(True)
        self._best_pairs_btn.setChecked(False)
        self._best_pairs_btn.setFixedWidth(90)
        self._best_pairs_btn.setStyleSheet(
            "QPushButton { background:#1e1e38; color:#ccc; border:1px solid #2a2a4a; padding:4px;"
            "             font-size:11px; border-radius:3px; }"
            "QPushButton:hover { background:#252555; }"
            "QPushButton:checked { background:#3a5a7a; color:#fff; }"
        )
        self._best_pairs_btn.clicked.connect(self._on_toggle_best_pairs)
        hdr.addWidget(self._best_pairs_btn)

        root.addLayout(hdr)

        self._current_data: Optional[dict] = None
        self._navigate_to_cat_callback = None  # Callback to navigate to a cat by name

        self._pairs_table = QTableWidget(0, 13)
        self._pairs_table.verticalHeader().setVisible(False)
        self._pairs_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._pairs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._pairs_table.setFocusPolicy(Qt.NoFocus)
        self._pairs_table.setWordWrap(False)
        self._pairs_table.setAlternatingRowColors(True)
        hh = self._pairs_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        for col in range(2, 13):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
        self._pairs_table.setColumnWidth(0, 120)
        self._pairs_table.setColumnWidth(1, 120)
        for col in range(2, 9):
            self._pairs_table.setColumnWidth(col, 40)
        self._pairs_table.setColumnWidth(9, 60)
        self._pairs_table.setColumnWidth(10, 50)
        self._pairs_table.setColumnWidth(11, 75)
        self._pairs_table.setColumnWidth(12, 50)
        self._pairs_table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:12px;
            }
            QTableWidget::item { padding:3px 4px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:11px; font-weight:bold;
            }
        """)
        self._pairs_table.itemClicked.connect(self._on_pair_cell_clicked)
        root.addWidget(self._pairs_table, 1)

        self._excluded_table = QTableWidget(0, 12)
        self._excluded_table.verticalHeader().setVisible(False)
        self._excluded_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._excluded_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._excluded_table.setFocusPolicy(Qt.NoFocus)
        self._excluded_table.setAlternatingRowColors(True)
        self._excluded_table.hide()
        ex_hh = self._excluded_table.horizontalHeader()
        ex_hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 9):
            ex_hh.setSectionResizeMode(col, QHeaderView.Fixed)
        for col in range(1, 8):
            self._excluded_table.setColumnWidth(col, 50)
        self._excluded_table.setColumnWidth(8, 60)
        for col in range(9, 12):
            self._excluded_table.setColumnWidth(col, 60)
        self._excluded_table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:12px;
            }
            QTableWidget::item { padding:3px 4px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:11px; font-weight:bold;
            }
        """)
        root.addWidget(self._excluded_table, 1)
        self.retranslate_ui()

    def retranslate_ui(self):
        self._summary.setText(_tr("room_optimizer.detail.summary_idle", default="Select a room to see pair details."))
        self._best_pairs_btn.setText(
            _tr("room_optimizer.detail.best_pairs", default="Best Pairs")
            if self._best_pairs_btn.isChecked()
            else _tr("room_optimizer.detail.all_pairs", default="All Pairs")
        )
        self._best_pairs_btn.setToolTip(_tr(
            "room_optimizer.detail.best_pairs.tooltip",
            default="Best Pairs: one unique match per cat\nAll Pairs: every valid combination",
        ))
        self._pairs_table.setHorizontalHeaderLabels([
            _tr("room_optimizer.detail.table.cat_a", default="Cat A"),
            _tr("room_optimizer.detail.table.cat_b", default="Cat B"),
            "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
            _tr("table.column.sum", default="Sum"),
            _tr("room_optimizer.detail.table.avg", default="Avg"),
            _tr("room_optimizer.detail.table.inbred_risk", default="Inbred Risk"),
            _tr("room_optimizer.detail.table.rank", default="Rank"),
        ])
        self._excluded_table.setHorizontalHeaderLabels([
            _tr("safe_breeding.table.cat", default="Cat"),
            "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
            _tr("table.column.sum", default="Sum"),
            _tr("table.column.aggression", default="Agg"),
            _tr("table.column.libido", default="Lib"),
            _tr("table.column.inbred", default="Inbred"),
        ])

    def _on_pair_cell_clicked(self, item):
        """Handle clicks on cat names to navigate to the cat in the main view."""
        col = self._pairs_table.column(item)
        # Only handle clicks on Cat A (column 0) or Cat B (column 1)
        if col not in (0, 1):
            return

        cat_name = item.text()
        if not cat_name or not self._navigate_to_cat_callback:
            return

        # Call the navigate callback with the cat name
        self._navigate_to_cat_callback(cat_name)

    def _on_toggle_best_pairs(self):
        """Re-render pairs table based on toggle state."""
        checked = self._best_pairs_btn.isChecked()
        self._best_pairs_btn.setText(
            _tr("room_optimizer.detail.best_pairs", default="Best Pairs")
            if checked else _tr("room_optimizer.detail.all_pairs", default="All Pairs")
        )
        if self._current_data:
            self.show_room(self._current_data)

    @staticmethod
    def _apply_best_pairs_filter(pairs: list[dict]) -> list[dict]:
        """Greedy non-overlapping pair selection. pairs must be pre-sorted best-first."""
        used = set()
        result = []
        for pair in pairs:
            a, b = pair["cat_a"], pair["cat_b"]
            if a not in used and b not in used:
                result.append(pair)
                used.add(a)
                used.add(b)
        return result

    @staticmethod
    def _range_background(lo: int, hi: int) -> QColor:
        base = STAT_COLORS.get(max(lo, hi), QColor(100, 100, 115))
        if lo != hi:
            return QColor(
                min(255, int(base.red() * 0.55) + 22),
                min(255, int(base.green() * 0.55) + 22),
                min(255, int(base.blue() * 0.55) + 22),
            )
        return QColor(
            min(255, int(base.red() * 0.7) + 18),
            min(255, int(base.green() * 0.7) + 18),
            min(255, int(base.blue() * 0.7) + 18),
        )

    def show_room(self, data: Optional[dict]):
        if not data:
            self._summary.setText(_tr("room_optimizer.detail.summary_idle", default="Select a room to see pair details."))
            self._summary.setToolTip("")
            self._pairs_table.setRowCount(0)
            self._pairs_table.show()
            self._excluded_table.hide()
            return

        self._current_data = data

        room = data.get("room", "Unknown")
        cats = data.get("cats", [])
        total_pairs = int(data.get("total_pairs", 0))
        avg_stats = float(data.get("avg_stats", 0))
        avg_risk = float(data.get("avg_risk", 0))
        pairs = data.get("pairs", [])
        excluded_cats = data.get("excluded_cats", [])
        excluded_cat_rows = data.get("excluded_cat_rows", [])

        if room == "Excluded":
            self._pairs_table.hide()
            self._excluded_table.show()
            self._summary.setText(_tr(
                "room_optimizer.detail.summary.excluded",
                default="Excluded Cats  |  {count} cats excluded from breeding calculations",
                count=len(excluded_cat_rows),
            ))
            self._summary.setToolTip(_tr(
                "room_optimizer.detail.summary.excluded_tooltip",
                default="Excluded cats are hidden from room optimizer breeding calculations.",
            ))
            self._excluded_table.setRowCount(len(excluded_cat_rows))
            for row_idx, cat_row in enumerate(excluded_cat_rows):
                name_item = QTableWidgetItem(cat_row["name"])
                self._excluded_table.setItem(row_idx, 0, name_item)
                for stat_col, stat in enumerate(STAT_NAMES, start=1):
                    value = int(cat_row["stats"].get(stat, 0))
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setBackground(QBrush(STAT_COLORS.get(value, QColor(100, 100, 115))))
                    self._excluded_table.setItem(row_idx, stat_col, item)
                sum_item = QTableWidgetItem(str(int(cat_row["sum"])))
                sum_item.setTextAlignment(Qt.AlignCenter)
                self._excluded_table.setItem(row_idx, 8, sum_item)
                for trait_col, trait_key in enumerate(("aggression", "libido", "inbredness"), start=9):
                    trait_text = cat_row["traits"][trait_key]
                    trait_display = trait_text.replace("average", "avg")
                    trait_item = QTableWidgetItem(trait_display)
                    trait_item.setTextAlignment(Qt.AlignCenter)
                    trait_item.setBackground(QBrush(_trait_level_color(trait_text)))
                    self._excluded_table.setItem(row_idx, trait_col, trait_item)
            return

        self._pairs_table.show()
        self._excluded_table.hide()

        def _compact_names(names: list[str], limit: int = 8) -> str:
            if len(names) <= limit:
                return ", ".join(names)
            shown = ", ".join(names[:limit])
            return f"{shown}, ... (+{len(names) - limit} more)"

        cats_text = _compact_names(cats)
        self._summary.setText(
            _tr(
                "room_optimizer.detail.summary.room",
                default="{room}  |  {pair_count} pairs  |  Avg: {avg_stats:.1f}  |  Risk: {avg_risk:.0f}%",
                room=room,
                pair_count=total_pairs,
                avg_stats=avg_stats,
                avg_risk=avg_risk,
            )
        )
        self._summary.setToolTip(
            _tr("room_optimizer.detail.summary.cats", default="Cats: {names}", names=", ".join(cats))
            if cats else ""
        )

        # Preserve original rank before filtering
        for i, pair in enumerate(pairs, 1):
            pair["_original_rank"] = i

        # Apply best pairs filter if enabled
        if self._best_pairs_btn.isChecked():
            pairs = self._apply_best_pairs_filter(pairs)

        self._pairs_table.setRowCount(len(pairs))
        for i, pair in enumerate(pairs, 1):
            # Cat A and B items with hyperlink styling
            cat_a_item = QTableWidgetItem(pair['cat_a'])
            cat_b_item = QTableWidgetItem(pair['cat_b'])
            # Style as hyperlinks
            hyperlink_color = QColor(0x5b9bd5)  # Blue
            cat_a_item.setForeground(QBrush(hyperlink_color))
            cat_b_item.setForeground(QBrush(hyperlink_color))
            font = cat_a_item.font()
            font.setUnderline(True)
            cat_a_item.setFont(font)
            cat_b_item.setFont(font)
            cat_a_item.setToolTip(_tr("common.tooltip.jump_to_alive", default="Click to jump to this cat in Alive Cats view"))
            cat_b_item.setToolTip(_tr("common.tooltip.jump_to_alive", default="Click to jump to this cat in Alive Cats view"))
            sum_lo, sum_hi = pair.get("sum_range", (0, 0))
            sum_item = QTableWidgetItem(f"{sum_lo}-{sum_hi}")
            sum_item.setToolTip(_tr("room_optimizer.detail.tooltip.sum_range", default="Possible offspring stat sum range: {lo} to {hi}", lo=sum_lo, hi=sum_hi))
            avg_item = QTableWidgetItem(f"{pair['avg_stats']:.1f}")
            stat_ranges = pair.get("stat_ranges", {})
            stat_items = []
            for stat in STAT_NAMES:
                lo, hi = stat_ranges.get(stat, (0, 0))
                item = QTableWidgetItem(f"{lo}-{hi}")
                item.setToolTip(_tr("room_optimizer.detail.tooltip.stat_range", default="{stat} offspring range: {lo} to {hi}", stat=stat.upper(), lo=lo, hi=hi))
                item.setBackground(QBrush(self._range_background(lo, hi)))
                stat_items.append(item)
            risk_item = QTableWidgetItem(f"{pair['risk']:.0f}%")
            rank_item = QTableWidgetItem(str(pair.get("_original_rank", i)))

            for item in stat_items:
                item.setTextAlignment(Qt.AlignCenter)
            sum_item.setTextAlignment(Qt.AlignCenter)
            avg_item.setTextAlignment(Qt.AlignCenter)
            risk_item.setTextAlignment(Qt.AlignCenter)
            rank_item.setTextAlignment(Qt.AlignCenter)
            sum_item.setBackground(QBrush(self._range_background(sum_lo // len(STAT_NAMES), sum_hi // len(STAT_NAMES))))
            avg_item.setBackground(QBrush(self._range_background(int(pair['avg_stats']), int(pair['avg_stats']))))

            risk = float(pair["risk"])
            if risk >= 50:
                risk_item.setForeground(QBrush(QColor(217, 119, 119)))
            elif risk >= 20:
                risk_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                risk_item.setForeground(QBrush(QColor(98, 194, 135)))

            self._pairs_table.setItem(i - 1, 0, cat_a_item)
            self._pairs_table.setItem(i - 1, 1, cat_b_item)
            for j, item in enumerate(stat_items, 2):
                self._pairs_table.setItem(i - 1, j, item)
            self._pairs_table.setItem(i - 1, 9, sum_item)
            self._pairs_table.setItem(i - 1, 10, avg_item)
            self._pairs_table.setItem(i - 1, 11, risk_item)
            self._pairs_table.setItem(i - 1, 12, rank_item)


class PerfectPlannerDetailPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:#0a0a18; border-top:1px solid #1e1e38;")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        self._summary = QLabel()
        self._summary.setStyleSheet("color:#aaa; font-size:12px;")
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        self._actions_table = QTableWidget(0, 6)
        self._actions_table.verticalHeader().setVisible(False)
        self._actions_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._actions_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._actions_table.setFocusPolicy(Qt.NoFocus)
        self._actions_table.setWordWrap(True)
        self._actions_table.setAlternatingRowColors(True)
        hh = self._actions_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        self._actions_table.setColumnWidth(2, 72)
        self._actions_table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:12px;
            }
            QTableWidget::item { padding:4px 5px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:11px; font-weight:bold;
            }
        """)
        root.addWidget(self._actions_table, 1)

        self._excluded_table = QTableWidget(0, 12)
        self._excluded_table.verticalHeader().setVisible(False)
        self._excluded_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._excluded_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._excluded_table.setFocusPolicy(Qt.NoFocus)
        self._excluded_table.setAlternatingRowColors(True)
        self._excluded_table.hide()
        ex_hh = self._excluded_table.horizontalHeader()
        ex_hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 9):
            ex_hh.setSectionResizeMode(col, QHeaderView.Fixed)
        for col in range(1, 8):
            self._excluded_table.setColumnWidth(col, 50)
        self._excluded_table.setColumnWidth(8, 60)
        for col in range(9, 12):
            self._excluded_table.setColumnWidth(col, 60)
        self._excluded_table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:12px;
            }
            QTableWidget::item { padding:3px 4px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:11px; font-weight:bold;
            }
        """)
        root.addWidget(self._excluded_table, 1)
        self.retranslate_ui()

    def retranslate_ui(self):
        self._summary.setText(_tr("perfect_planner.detail.summary_idle", default="Select a stage to see the plan details."))
        self._actions_table.setHorizontalHeaderLabels([
            _tr("common.action", default="Action"),
            _tr("common.target", default="Target"),
            _tr("table.column.risk", default="Risk%"),
            _tr("perfect_planner.detail.table.why", default="Why"),
            _tr("common.children", default="Children"),
            _tr("common.rotate", default="Rotate"),
        ])
        self._excluded_table.setHorizontalHeaderLabels([
            _tr("safe_breeding.table.cat", default="Cat"),
            "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
            _tr("table.column.sum", default="Sum"),
            _tr("table.column.aggression", default="Agg"),
            _tr("table.column.libido", default="Lib"),
            _tr("table.column.inbred", default="Inbred"),
        ])

    @staticmethod
    def _build_target_grid(action: dict) -> QWidget:
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)

        target_grid = action.get("target_grid") or {}
        parents = target_grid.get("parents", [])
        offspring = target_grid.get("offspring", {})

        name_col_width = 96
        for row_idx, header in enumerate(["", *STAT_NAMES, "Sum"]):
            if row_idx == 0:
                continue
            hdr = QLabel(header)
            hdr.setAlignment(Qt.AlignCenter)
            hdr.setStyleSheet("color:#6f7fa0; font-size:10px; font-weight:bold;")
            grid.addWidget(hdr, 0, row_idx)

        def _parent_row(row: int, parent: dict):
            name = QLabel(parent.get("name", ""))
            name.setWordWrap(True)
            name.setMinimumWidth(name_col_width)
            name.setStyleSheet("color:#ddd; font-size:11px; font-weight:bold;")
            grid.addWidget(name, row, 0)
            for col, stat in enumerate(STAT_NAMES, 1):
                value = int(parent.get("stats", {}).get(stat, 0))
                c = STAT_COLORS.get(value, QColor(100, 100, 115))
                lbl = QLabel(str(value))
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet(
                    f"background:rgb({c.red()},{c.green()},{c.blue()});"
                    "color:#fff; font-size:11px; font-weight:bold;"
                    "border-radius:2px; padding:2px 6px;"
                )
                grid.addWidget(lbl, row, col)
            sum_lbl = QLabel(str(int(parent.get("sum", 0))))
            sum_lbl.setAlignment(Qt.AlignCenter)
            sum_lbl.setStyleSheet("color:#9aa6ba; font-size:11px; font-weight:bold;")
            grid.addWidget(sum_lbl, row, len(STAT_NAMES) + 1)

        def _offspring_row(row: int, info: dict):
            name = QLabel(_tr("detail.label.offspring", default="Offspring"))
            name.setStyleSheet("color:#777; font-size:10px; font-style:italic;")
            grid.addWidget(name, row, 0)
            sum_lo, sum_hi = info.get("sum_range", (0, 0))
            for col, stat in enumerate(STAT_NAMES, 1):
                stat_info = info.get("stats", {}).get(stat, {})
                lo = int(stat_info.get("lo", 0))
                hi = int(stat_info.get("hi", 0))
                expected = float(stat_info.get("expected", hi))
                hi_color = STAT_COLORS.get(hi, QColor(100, 100, 115))
                if lo == hi:
                    text = f"{lo}"
                else:
                    text = f"{lo}-{hi}\n{expected:.1f}"
                lbl = QLabel(text)
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setToolTip(_tr("perfect_planner.detail.tooltip.stat_range", default="{stat}: {lo}-{hi}, expected {expected:.1f}", stat=stat, lo=lo, hi=hi, expected=expected))
                lbl.setStyleSheet(
                    f"background:rgba({hi_color.red()},{hi_color.green()},{hi_color.blue()},110);"
                    f"color:rgb({hi_color.red()},{hi_color.green()},{hi_color.blue()});"
                    "font-size:10px; font-weight:bold; border-radius:2px; padding:2px 4px;"
                )
                grid.addWidget(lbl, row, col)
            if sum_lo == sum_hi:
                sum_text = str(sum_lo)
            else:
                sum_text = f"{sum_lo}-{sum_hi}"
            sum_lbl = QLabel(sum_text)
            sum_lbl.setAlignment(Qt.AlignCenter)
            sum_lbl.setStyleSheet("color:#777; font-size:11px; font-weight:bold;")
            grid.addWidget(sum_lbl, row, len(STAT_NAMES) + 1)

        if len(parents) >= 1:
            _parent_row(1, parents[0])
        if len(parents) >= 2:
            _parent_row(2, parents[1])
        _offspring_row(3, offspring)
        return container

    def show_stage(self, data: Optional[dict]):
        if not data:
            self._summary.setText(_tr("perfect_planner.detail.summary_idle", default="Select a stage to see the plan details."))
            self._summary.setToolTip("")
            self._actions_table.setRowCount(0)
            self._actions_table.show()
            self._excluded_table.hide()
            return

        if data.get("stage") == "Excluded":
            rows = data.get("excluded_cat_rows", [])
            self._summary.setText(_tr(
                "perfect_planner.detail.summary.excluded",
                default="Excluded Cats  |  {count} cats excluded from planner calculations",
                count=len(rows),
            ))
            self._summary.setToolTip(_tr(
                "perfect_planner.detail.summary.excluded_tooltip",
                default="Excluded cats are hidden from the Perfect 7 Planner calculations.",
            ))
            self._actions_table.hide()
            self._excluded_table.show()
            self._excluded_table.setRowCount(len(rows))
            for row_idx, cat_row in enumerate(rows):
                name_item = QTableWidgetItem(cat_row["name"])
                self._excluded_table.setItem(row_idx, 0, name_item)
                for stat_col, stat in enumerate(STAT_NAMES, start=1):
                    value = int(cat_row["stats"].get(stat, 0))
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setBackground(QBrush(STAT_COLORS.get(value, QColor(100, 100, 115))))
                    self._excluded_table.setItem(row_idx, stat_col, item)
                sum_item = QTableWidgetItem(str(int(cat_row["sum"])))
                sum_item.setTextAlignment(Qt.AlignCenter)
                self._excluded_table.setItem(row_idx, 8, sum_item)
                for trait_col, trait_key in enumerate(("aggression", "libido", "inbredness"), start=9):
                    trait_text = cat_row["traits"][trait_key]
                    trait_display = trait_text.replace("average", "avg")
                    trait_item = QTableWidgetItem(trait_display)
                    trait_item.setTextAlignment(Qt.AlignCenter)
                    trait_item.setBackground(QBrush(_trait_level_color(trait_text)))
                    self._excluded_table.setItem(row_idx, trait_col, trait_item)
            return

        self._actions_table.show()
        self._excluded_table.hide()

        self._summary.setText(data.get("summary", ""))
        notes = data.get("notes", [])
        self._summary.setToolTip("\n".join(notes))

        actions = data.get("actions", [])
        self._actions_table.setRowCount(len(actions))
        for row, action in enumerate(actions):
            action_item = QTableWidgetItem(action.get("action", ""))
            risk_value = action.get("risk")
            risk_item = QTableWidgetItem("—" if risk_value is None else f"{float(risk_value):.0f}%")
            why_item = QTableWidgetItem(action.get("why", ""))
            children_item = QTableWidgetItem(action.get("children", ""))
            rotate_item = QTableWidgetItem(action.get("rotate", ""))

            risk_item.setTextAlignment(Qt.AlignCenter)
            if risk_value is not None:
                risk = float(risk_value)
                if risk >= 50:
                    risk_item.setForeground(QBrush(QColor(217, 119, 119)))
                elif risk >= 20:
                    risk_item.setForeground(QBrush(QColor(216, 181, 106)))
                else:
                    risk_item.setForeground(QBrush(QColor(98, 194, 135)))

            self._actions_table.setItem(row, 0, action_item)
            if action.get("target_grid"):
                self._actions_table.setCellWidget(row, 1, self._build_target_grid(action))
            else:
                self._actions_table.setItem(row, 1, QTableWidgetItem(action.get("target", "")))
            self._actions_table.setItem(row, 2, risk_item)
            self._actions_table.setItem(row, 3, why_item)
            self._actions_table.setItem(row, 4, children_item)
            self._actions_table.setItem(row, 5, rotate_item)

        self._actions_table.resizeRowsToContents()


class PerfectCatPlannerView(QWidget):
    """Stage-based planner for building perfect 7-base-stat lines."""

    @staticmethod
    def _set_toggle_button_label(btn: QPushButton, label_key: str, default_label: str):
        _set_localized_toggle_button_label(btn, label_key, default_label)

    @staticmethod
    def _bind_persistent_toggle(btn: QPushButton, label_key: str, key: str, default_label: str):
        PerfectCatPlannerView._set_toggle_button_label(btn, label_key, default_label)
        btn.toggled.connect(lambda checked: _set_optimizer_flag(key, checked))
        btn.toggled.connect(lambda _: PerfectCatPlannerView._set_toggle_button_label(btn, label_key, default_label))

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
            "QSpinBox { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:3px 6px; }"
        )
        self._cats: list[Cat] = []
        self._excluded_keys: set[int] = set()
        self._cache: Optional[BreedingCache] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        header = QHBoxLayout()
        self._title = QLabel()
        self._title.setStyleSheet("color:#ddd; font-size:18px; font-weight:bold;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(self._title)
        header.addStretch()
        header.addWidget(self._summary)
        root.addLayout(header)

        desc = QLabel()
        self._desc = desc
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#8d8da8; font-size:11px;")
        root.addWidget(desc)

        controls_wrap = QScrollArea()
        controls_wrap.setWidgetResizable(True)
        controls_wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        controls_wrap.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_wrap.setFrameShape(QFrame.NoFrame)
        controls_wrap.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        controls_box = QWidget()
        controls = QHBoxLayout(controls_box)
        controls.setSpacing(8)
        controls.setContentsMargins(0, 0, 0, 0)

        self._min_stats_label = QLabel()
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

        controls.addSpacing(12)

        self._max_risk_label = QLabel()
        self._max_risk_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._max_risk_label)

        self._max_risk_input = QLineEdit()
        self._max_risk_input.setPlaceholderText("10")
        self._max_risk_input.setFixedWidth(60)
        self._max_risk_input.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        controls.addWidget(self._max_risk_input)

        controls.addSpacing(12)

        self._starter_label = QLabel()
        self._starter_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._starter_label)
        self._starter_pairs_input = QSpinBox()
        self._starter_pairs_input.setRange(1, 12)
        self._starter_pairs_input.setValue(4)
        self._starter_pairs_input.setFixedWidth(60)
        controls.addWidget(self._starter_pairs_input)

        controls.addSpacing(12)

        self._stimulation_label = QLabel()
        self._stimulation_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._stimulation_label)
        self._stimulation_input = QSpinBox()
        self._stimulation_input.setRange(0, 200)
        self._stimulation_input.setValue(50)
        self._stimulation_input.setFixedWidth(70)
        controls.addWidget(self._stimulation_input)

        controls.addSpacing(12)

        self._plan_btn = QPushButton()
        self._plan_btn.setStyleSheet(
            "QPushButton { background:#1f5f4a; color:#f2f7f3; border:1px solid #3f8f72; "
            "border-radius:4px; padding:6px 14px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#26735a; }"
            "QPushButton:pressed { background:#184b3a; }"
        )
        self._plan_btn.clicked.connect(self._calculate_plan)
        controls.addWidget(self._plan_btn)

        controls.addSpacing(12)

        self._avoid_lovers_checkbox = QPushButton()
        self._avoid_lovers_checkbox.setCheckable(True)
        self._avoid_lovers_checkbox.setChecked(_saved_optimizer_flag("perfect_planner_avoid_lovers", False))
        self._avoid_lovers_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#5a3a2a; color:#ddd; border:1px solid #8a5a4a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(self._avoid_lovers_checkbox, "perfect_planner.toggle.avoid_lovers", "perfect_planner_avoid_lovers", "Avoid Lovers")
        controls.addWidget(self._avoid_lovers_checkbox)

        self._prefer_low_aggression_checkbox = QPushButton()
        self._prefer_low_aggression_checkbox.setCheckable(True)
        self._prefer_low_aggression_checkbox.setChecked(_saved_optimizer_flag("prefer_low_aggression", True))
        self._prefer_low_aggression_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#4a2a2a; color:#ddd; border:1px solid #7a4a4a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(
            self._prefer_low_aggression_checkbox,
            "perfect_planner.toggle.prefer_low_aggression",
            "prefer_low_aggression",
            "Prefer Low Aggression",
        )
        controls.addWidget(self._prefer_low_aggression_checkbox)

        self._prefer_high_libido_checkbox = QPushButton()
        self._prefer_high_libido_checkbox.setCheckable(True)
        self._prefer_high_libido_checkbox.setChecked(_saved_optimizer_flag("prefer_high_libido", True))
        self._prefer_high_libido_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#2a4a36; color:#ddd; border:1px solid #4a7a5a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(
            self._prefer_high_libido_checkbox,
            "perfect_planner.toggle.prefer_high_libido",
            "prefer_high_libido",
            "Prefer High Libido",
        )
        controls.addWidget(self._prefer_high_libido_checkbox)

        controls.addStretch()
        controls_wrap.setWidget(controls_box)
        root.addWidget(controls_wrap)

        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setStyleSheet("QSplitter::handle:vertical { background:#1e1e38; }")

        self._table = QTableWidget(0, 6)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        self._table.setColumnWidth(2, 70)
        self._table.setColumnWidth(3, 95)
        self._table.setColumnWidth(4, 70)
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self._splitter.addWidget(self._table)

        self._bottom_tabs = QTabWidget()
        self._bottom_tabs.setStyleSheet(
            "QTabWidget::pane { border:1px solid #1e1e38; background:#0a0a18; }"
            "QTabBar::tab { background:#14142a; color:#888; padding:6px 14px; border:1px solid #1e1e38;"
            " border-bottom:none; margin-right:2px; font-size:11px; }"
            "QTabBar::tab:selected { background:#1a1a36; color:#ddd; font-weight:bold; }"
            "QTabBar::tab:hover { background:#1e1e3a; color:#bbb; }"
        )

        self._details_pane = PerfectPlannerDetailPanel()
        self._bottom_tabs.addTab(self._details_pane, "")

        self._cat_locator = RoomOptimizerCatLocator()
        self._bottom_tabs.addTab(self._cat_locator, "")

        self._splitter.addWidget(self._bottom_tabs)
        self._splitter.setSizes([180, 420])
        root.addWidget(self._splitter, 1)

        self.retranslate_ui()
        _enforce_min_font_in_widget_tree(self)

    def retranslate_ui(self):
        self._title.setText(_tr("perfect_planner.title", default="Perfect 7 Planner"))
        self._desc.setText(_tr(
            "perfect_planner.description",
            default="Build a staged plan for pushing toward full 7-base-stat lines without family breeding. Use Max inbreeding risk = 0 to force fully clean plans.",
        ))
        self._min_stats_label.setText(_tr("room_optimizer.min_base_stats", default="Min base stats:"))
        self._max_risk_label.setText(_tr("room_optimizer.max_inbreeding_risk", default="Max inbreeding risk %:"))
        self._starter_label.setText(_tr("perfect_planner.start_pairs", default="Start pairs:"))
        self._starter_pairs_input.setToolTip(_tr(
            "perfect_planner.start_pairs.tooltip",
            default="How many unrelated foundation pairs the planner should recommend to start the line.",
        ))
        self._stimulation_label.setText(_tr("detail.label.stimulation", default="Stimulation:"))
        self._stimulation_input.setToolTip(_tr(
            "perfect_planner.stimulation.tooltip",
            default="Used for projected offspring weighting. Higher stimulation favors the stronger parent stat.",
        ))
        self._plan_btn.setText(_tr("perfect_planner.build_plan", default="Build Perfect 7 Plan"))
        self._table.setHorizontalHeaderLabels([
            _tr("common.stage", default="Stage"),
            _tr("common.goal", default="Goal"),
            _tr("room_optimizer.table.expected_pairs", default="Pairs"),
            _tr("common.coverage", default="7+ Coverage"),
            _tr("table.column.risk", default="Risk%"),
            _tr("common.details", default="Details"),
        ])
        self._bottom_tabs.setTabText(self._bottom_tabs.indexOf(self._details_pane), _tr("perfect_planner.tab.stage_details", default="Stage Details"))
        self._bottom_tabs.setTabText(self._bottom_tabs.indexOf(self._cat_locator), _tr("room_optimizer.tab.cat_locator", default="Cat Locator"))
        self._details_pane.retranslate_ui()
        self._cat_locator.retranslate_ui()

    def _on_table_selection_changed(self):
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            self._details_pane.show_stage(None)
            return
        row = selected_ranges[0].topRow()
        stage_item = self._table.item(row, 0)
        if stage_item:
            data = stage_item.data(Qt.UserRole)
            self._details_pane.show_stage(data if isinstance(data, dict) else None)

    def set_cats(self, cats: list[Cat], excluded_keys: set[int] = None):
        self._cats = cats
        blacklisted_keys = {c.db_key for c in cats if c.is_blacklisted}
        self._excluded_keys = (excluded_keys or set()) | blacklisted_keys
        alive_count = len([c for c in cats if c.status != "Gone"])
        excluded_count = len([c for c in cats if c.status != "Gone" and c.db_key in self._excluded_keys])
        if excluded_count > 0:
            self._summary.setText(_tr(
                "room_optimizer.summary.available_excluded",
                default="{alive_count} alive cats available ({excluded_count} excluded from breeding)",
                alive_count=alive_count,
                excluded_count=excluded_count,
            ))
        else:
            self._summary.setText(_tr(
                "room_optimizer.summary.available",
                default="{alive_count} alive cats available",
                alive_count=alive_count,
            ))

    def set_cache(self, cache: Optional['BreedingCache']):
        self._cache = cache

    def _calculate_plan(self):
        excluded_keys = getattr(self, "_excluded_keys", set())
        alive_cats = [c for c in self._cats if c.status != "Gone" and c.db_key not in excluded_keys]
        excluded_cats = [c for c in self._cats if c.status != "Gone" and c.db_key in excluded_keys]

        min_stats = 0
        try:
            if self._min_stats_input.text().strip():
                min_stats = int(self._min_stats_input.text().strip())
        except ValueError:
            pass

        max_risk = 10.0
        try:
            if self._max_risk_input.text().strip():
                max_risk = float(self._max_risk_input.text().strip())
        except ValueError:
            pass

        starter_pairs = int(self._starter_pairs_input.value())
        stimulation = float(self._stimulation_input.value())
        avoid_lovers = self._avoid_lovers_checkbox.isChecked()
        prefer_low_aggression = self._prefer_low_aggression_checkbox.isChecked()
        prefer_high_libido = self._prefer_high_libido_checkbox.isChecked()

        if min_stats > 0:
            alive_cats = [c for c in alive_cats if sum(c.base_stats.values()) >= min_stats]

        if len(alive_cats) < 2:
            self._table.setRowCount(0)
            self._details_pane.show_stage(None)
            self._cat_locator.clear()
            self._summary.setText(_tr("perfect_planner.summary.not_enough_cats", default="Not enough cats to build a plan"))
            return

        stat_sum = {cat.db_key: sum(cat.base_stats.values()) for cat in alive_cats}
        cache = self._cache
        parent_key_map = {
            cat.db_key: {parent.db_key for parent in get_parents(cat)}
            for cat in alive_cats
        }
        hater_key_map = {
            cat.db_key: {other.db_key for other in getattr(cat, "haters", [])}
            for cat in alive_cats
        }
        lover_key_map = {
            cat.db_key: {other.db_key for other in getattr(cat, "lovers", [])}
            for cat in alive_cats
        }
        pair_eval_cache: dict[tuple[int, int], tuple[bool, str, float]] = {}

        better_stat_chance = (1.0 + 0.01 * stimulation) / (2.0 + 0.01 * stimulation)

        def _pair_key(cat_a: Cat, cat_b: Cat) -> tuple[int, int]:
            a_key, b_key = cat_a.db_key, cat_b.db_key
            return (a_key, b_key) if a_key < b_key else (b_key, a_key)

        def _is_hater_conflict(cat_a: Cat, cat_b: Cat) -> bool:
            haters_a = hater_key_map.get(cat_a.db_key, set())
            haters_b = hater_key_map.get(cat_b.db_key, set())
            return cat_b.db_key in haters_a or cat_a.db_key in haters_b

        def _is_lover_conflict(cat_a: Cat, cat_b: Cat) -> bool:
            if not avoid_lovers:
                return False
            lovers_a = lover_key_map.get(cat_a.db_key, set())
            lovers_b = lover_key_map.get(cat_b.db_key, set())
            if lovers_a and cat_b.db_key not in lovers_a:
                return True
            if lovers_b and cat_a.db_key not in lovers_b:
                return True
            return False

        def _trait_or_default(value: Optional[float], default: float = 0.5) -> float:
            if value is None:
                return default
            return max(0.0, min(1.0, float(value)))

        def _personality_bonus(cat_a: Cat, cat_b: Optional[Cat] = None) -> float:
            cats = [cat_a] if cat_b is None else [cat_a, cat_b]
            score = 0.0
            if prefer_low_aggression:
                score += sum(1.0 - _trait_or_default(cat.aggression) for cat in cats) / len(cats)
            if prefer_high_libido:
                score += sum(_trait_or_default(cat.libido) for cat in cats) / len(cats)
            return score

        def _is_direct_family_pair(cat_a: Cat, cat_b: Cat) -> bool:
            parents_a = parent_key_map.get(cat_a.db_key, set())
            parents_b = parent_key_map.get(cat_b.db_key, set())
            if cat_a.db_key in parents_b or cat_b.db_key in parents_a:
                return True
            return bool(parents_a & parents_b)

        def _pair_eval(cat_a: Cat, cat_b: Cat) -> tuple[bool, str, float]:
            key = _pair_key(cat_a, cat_b)
            cached = pair_eval_cache.get(key)
            if cached is not None:
                return cached
            ok, reason = can_breed(cat_a, cat_b)
            if ok and _is_direct_family_pair(cat_a, cat_b):
                ok = False
                reason = "Direct family pair"
            if ok and _is_hater_conflict(cat_a, cat_b):
                ok = False
                reason = "These cats hate each other"
            if ok and _is_lover_conflict(cat_a, cat_b):
                ok = False
                reason = "One or both cats already have a lover"
            if ok:
                if cache is not None and cache.ready:
                    risk = cache.risk_pct.get(cache._pair_key(cat_a.db_key, cat_b.db_key), 0.0)
                else:
                    risk = risk_percent(cat_a, cat_b)
            else:
                risk = 0.0
            pair_eval_cache[key] = (ok, reason, risk)
            return pair_eval_cache[key]

        def _offspring_projection(cat_a: Cat, cat_b: Cat) -> dict:
            expected_stats: dict[str, float] = {}
            stat_ranges: dict[str, tuple[int, int]] = {}
            locked_stats: list[str] = []
            reachable_stats: list[str] = []
            missing_stats: list[str] = []
            seven_plus_total = 0.0
            distance_total = 0.0
            for stat in STAT_NAMES:
                stat_a = cat_a.base_stats[stat]
                stat_b = cat_b.base_stats[stat]
                lo = min(stat_a, stat_b)
                hi = max(stat_a, stat_b)
                stat_ranges[stat] = (lo, hi)
                expected = hi * better_stat_chance + lo * (1.0 - better_stat_chance)
                expected_stats[stat] = expected
                distance_total += abs(expected - 7.0)
                if lo >= 7:
                    locked_stats.append(stat)
                    reachable_stats.append(stat)
                    seven_plus_total += 1.0
                elif hi >= 7:
                    reachable_stats.append(stat)
                    seven_plus_total += better_stat_chance
                else:
                    missing_stats.append(stat)
            sum_lo = sum(lo for lo, _ in stat_ranges.values())
            sum_hi = sum(hi for _, hi in stat_ranges.values())
            avg_expected = sum(expected_stats.values()) / len(STAT_NAMES)
            return {
                "expected_stats": expected_stats,
                "stat_ranges": stat_ranges,
                "locked_stats": locked_stats,
                "reachable_stats": reachable_stats,
                "missing_stats": missing_stats,
                "seven_plus_total": seven_plus_total,
                "distance_total": distance_total,
                "sum_range": (sum_lo, sum_hi),
                "avg_expected": avg_expected,
            }

        candidate_pairs: list[tuple[Cat, Cat]] = []
        for i, cat_a in enumerate(alive_cats):
            for cat_b in alive_cats[i + 1:]:
                ok, _ = can_breed(cat_a, cat_b)
                if ok:
                    candidate_pairs.append((cat_a, cat_b))

        evaluated_pairs = []
        for cat_a, cat_b in candidate_pairs:
            ok, _, risk = _pair_eval(cat_a, cat_b)
            if not ok or risk > max_risk:
                continue

            projection = _offspring_projection(cat_a, cat_b)
            founder_bonus = sum(1.0 for cat in (cat_a, cat_b) if not get_parents(cat)) * 2.0
            must_breed_bonus = 3.0 if cat_a.must_breed or cat_b.must_breed else 0.0
            personality = _personality_bonus(cat_a, cat_b) * 3.0
            progress_score = (
                projection["seven_plus_total"] * 16.0
                + len(projection["locked_stats"]) * 12.0
                + len(projection["reachable_stats"]) * 6.0
                - len(projection["missing_stats"]) * 7.0
                - projection["distance_total"] * 2.5
                - risk * 1.2
                + founder_bonus
                + personality
                + must_breed_bonus
            )

            evaluated_pairs.append({
                "cat_a": cat_a,
                "cat_b": cat_b,
                "risk": risk,
                "projection": projection,
                "score": progress_score,
                "personality": personality,
            })

        evaluated_pairs.sort(
            key=lambda pair: (
                pair["projection"]["seven_plus_total"],
                len(pair["projection"]["locked_stats"]),
                pair["score"],
                stat_sum[pair["cat_a"].db_key] + stat_sum[pair["cat_b"].db_key],
            ),
            reverse=True,
        )

        selected_pairs = []
        used_keys: set[int] = set()
        for pair in evaluated_pairs:
            cat_a = pair["cat_a"]
            cat_b = pair["cat_b"]
            if cat_a.db_key in used_keys or cat_b.db_key in used_keys:
                continue
            selected_pairs.append(pair)
            used_keys.add(cat_a.db_key)
            used_keys.add(cat_b.db_key)
            if len(selected_pairs) >= starter_pairs:
                break

        if not selected_pairs:
            self._table.setRowCount(0)
            self._details_pane.show_stage(None)
            self._cat_locator.clear()
            self._summary.setText(_tr(
                "perfect_planner.summary.no_pairs",
                default="No low-risk unrelated breeding pairs found under the current filters",
            ))
            return

        def _fmt_stats(stats: list[str]) -> str:
            return ", ".join(stats) if stats else _tr("perfect_planner.none", default="none")

        def _pair_name(pair: dict) -> str:
            return f"{pair['cat_a'].name} ({pair['cat_a'].gender_display}) x {pair['cat_b'].name} ({pair['cat_b'].gender_display})"

        def _pair_stage_label(idx: int) -> str:
            return _tr("perfect_planner.stage.pair", default="Pair {index}", index=idx)

        def _rotation_stage_label(idx: int) -> str:
            return _tr("perfect_planner.stage.rotation", default="Rotation {index}", index=idx)

        def _stage1_target_grid(pair: dict) -> dict:
            projection = pair["projection"]
            return {
                "parents": [
                    {
                        "name": f"{pair['cat_a'].name}\n{pair['cat_a'].gender_display}",
                        "stats": pair["cat_a"].base_stats,
                        "sum": sum(pair["cat_a"].base_stats.values()),
                    },
                    {
                        "name": f"{pair['cat_b'].name}\n{pair['cat_b'].gender_display}",
                        "stats": pair["cat_b"].base_stats,
                        "sum": sum(pair["cat_b"].base_stats.values()),
                    },
                ],
                "offspring": {
                    "stats": {
                        stat: {
                            "lo": projection["stat_ranges"][stat][0],
                            "hi": projection["stat_ranges"][stat][1],
                            "expected": projection["expected_stats"][stat],
                        }
                        for stat in STAT_NAMES
                    },
                    "sum_range": projection["sum_range"],
                },
            }

        def _planner_pair_grid(cat_a: Cat, cat_b: Cat, projection: dict) -> dict:
            return {
                "parents": [
                    {
                        "name": f"{cat_a.name}\n{cat_a.gender_display}",
                        "stats": cat_a.base_stats,
                        "sum": sum(cat_a.base_stats.values()),
                    },
                    {
                        "name": f"{cat_b.name}\n{cat_b.gender_display}",
                        "stats": cat_b.base_stats,
                        "sum": sum(cat_b.base_stats.values()),
                    },
                ],
                "offspring": {
                    "stats": {
                        stat: {
                            "lo": projection["stat_ranges"][stat][0],
                            "hi": projection["stat_ranges"][stat][1],
                            "expected": projection["expected_stats"][stat],
                        }
                        for stat in STAT_NAMES
                    },
                    "sum_range": projection["sum_range"],
                },
            }

        def _rotation_candidate(pair: dict) -> Optional[dict]:
            missing_stats = pair["projection"]["missing_stats"]
            if not missing_stats:
                return None
            best = None
            pair_cats = {pair["cat_a"].db_key, pair["cat_b"].db_key}
            for parent in (pair["cat_a"], pair["cat_b"]):
                for candidate in alive_cats:
                    if candidate.db_key in pair_cats:
                        continue
                    ok, _, risk = _pair_eval(parent, candidate)
                    if not ok or risk > max_risk:
                        continue
                    bring_stats = [stat for stat in missing_stats if candidate.base_stats[stat] >= 7]
                    if not bring_stats:
                        continue
                    score = (
                        len(bring_stats) * 15.0
                        + sum(candidate.base_stats[stat] for stat in bring_stats)
                        - risk
                        + _personality_bonus(parent, candidate) * 3.0
                        + (4.0 if not get_parents(candidate) else 0.0)
                    )
                    record = {
                        "parent": parent,
                        "candidate": candidate,
                        "risk": risk,
                        "bring_stats": bring_stats,
                        "score": score,
                    }
                    if best is None or record["score"] > best["score"]:
                        best = record
            return best

        stage_rows: list[dict] = []

        stage1_actions = []
        for idx, pair in enumerate(selected_pairs, 1):
            projection = pair["projection"]
            bp = _pair_breakpoint_analysis(pair["cat_a"], pair["cat_b"], stimulation)
            stage1_actions.append({
                "action": _pair_stage_label(idx),
                "target": _pair_name(pair),
                "target_grid": _stage1_target_grid(pair),
                "risk": pair["risk"],
                "why": (
                    _tr(
                        "perfect_planner.stage1.action.why_intro",
                        default="Fast foundation pair for a perfect-7 line. Projected 7+ coverage: {coverage:.1f}/7 at stimulation {stim}. Breakpoint: {headline}. ",
                        coverage=projection["seven_plus_total"],
                        stim=int(stimulation),
                        headline=bp["headline"],
                    )
                    + " ".join(bp["hints"][:2])
                ),
                "children": (
                    _tr(
                        "perfect_planner.stage1.action.children",
                        default="Keep the strongest son and daughter if possible. Do not reuse siblings together.",
                    )
                ),
                "rotate": (
                    _tr(
                        "perfect_planner.stage1.action.rotate",
                        default="Stay on this pair until the line stops improving, then use the Stage 3 outcross.",
                    )
                ),
            })

        stage_rows.append({
            "stage": _tr("perfect_planner.stage1.title", default="Stage 1"),
            "goal": _tr(
                "perfect_planner.stage1.goal",
                default="Start with {count} foundation pairs",
                count=len(selected_pairs),
            ),
            "pairs": len(selected_pairs),
            "coverage": sum(pair["projection"]["seven_plus_total"] for pair in selected_pairs) / len(selected_pairs),
            "risk": max(pair["risk"] for pair in selected_pairs),
            "details": _tr(
                "perfect_planner.stage1.details",
                default="Best unrelated pairs to start pushing 7s immediately",
            ),
            "summary": (
                _tr(
                    "perfect_planner.stage1.summary",
                    default="Start with {count} unrelated foundation pairs. These are the fastest low-risk lines for reaching full 7-base-stat coverage.",
                    count=len(selected_pairs),
                )
            ),
            "notes": [
                _tr(
                    "perfect_planner.stage1.note.disjoint",
                    default="Foundation pairs are disjoint so you can work multiple lines at once.",
                ),
                _tr(
                    "perfect_planner.stage1.note.risk_cap",
                    default="Max inbreeding risk is enforced before a pair can appear here.",
                ),
            ],
            "actions": stage1_actions,
        })

        stage2_actions = []
        for idx, pair in enumerate(selected_pairs, 1):
            projection = pair["projection"]
            stage2_actions.append({
                "action": _tr(
                    "perfect_planner.stage2.action.title",
                    default="Separate Pair {index} offspring",
                    index=idx,
                ),
                "target": _tr(
                    "perfect_planner.stage2.action.target",
                    default="Protect locked stats: {stats}",
                    stats=_fmt_stats(projection["locked_stats"]),
                ),
                "risk": None,
                "why": (
                    _tr(
                        "perfect_planner.stage2.action.why",
                        default="Keeping sons and daughters apart preserves multiple clean branches instead of collapsing into sibling breeding.",
                    )
                ),
                "children": (
                    _tr(
                        "perfect_planner.stage2.action.children",
                        default="Move Pair {index} sons and daughters into different rooms from each other and from {parent_a} / {parent_b} once they mature.",
                        index=idx,
                        parent_a=pair["cat_a"].name,
                        parent_b=pair["cat_b"].name,
                    )
                ),
                "rotate": (
                    _tr(
                        "perfect_planner.stage2.action.rotate",
                        default="Choose one keeper line per sex, then hold the backups aside for future outcrosses.",
                    )
                ),
            })

        stage_rows.append({
            "stage": _tr("perfect_planner.stage2.title", default="Stage 2"),
            "goal": _tr("perfect_planner.stage2.goal", default="Separate children into future lines"),
            "pairs": len(stage2_actions),
            "coverage": sum(len(pair["projection"]["locked_stats"]) for pair in selected_pairs) / len(selected_pairs),
            "risk": 0.0,
            "details": _tr(
                "perfect_planner.stage2.details",
                default="Room guidance to avoid collapsing the plan into sibling loops",
            ),
            "summary": (
                _tr(
                    "perfect_planner.stage2.summary",
                    default="Separate offspring by line and sex. The planner assumes you will keep clean branches available for the next generation instead of breeding within one family.",
                )
            ),
            "notes": [
                _tr(
                    "perfect_planner.stage2.note.guidance",
                    default="This stage is the child-separation guidance from issue #19.",
                ),
                _tr(
                    "perfect_planner.stage2.note.keep_one",
                    default="If you only keep one kitten, keep the one that raises the lowest missing stat.",
                ),
            ],
            "actions": stage2_actions,
        })

        stage3_actions = []
        stage3_import_counts: list[float] = []
        for idx, pair in enumerate(selected_pairs, 1):
            rotation = _rotation_candidate(pair)
            missing = pair["projection"]["missing_stats"]
            if rotation is None:
                stage3_import_counts.append(0.0)
                stage3_actions.append({
                    "action": _tr(
                        "perfect_planner.stage3.action.defer_title",
                        default="Rotate Pair {index} later",
                        index=idx,
                    ),
                    "target": _tr(
                        "perfect_planner.stage3.action.defer_target",
                        default="Still missing: {stats}",
                        stats=_fmt_stats(missing),
                    ),
                    "risk": None,
                    "why": (
                        _tr(
                            "perfect_planner.stage3.action.defer_why",
                            default="No clean outcross in the current roster covers the missing stats under the active risk cap.",
                        )
                    ),
                    "children": (
                        _tr(
                            "perfect_planner.stage3.action.defer_children",
                            default="Keep the best backup kitten alive and wait for an unrelated stray or future outcross.",
                        )
                    ),
                    "rotate": (
                        _tr(
                            "perfect_planner.stage3.action.defer_rotate",
                            default="Bring in a stray/unrelated breeder with 7s in {stats}.",
                            stats=_fmt_stats(missing),
                        )
                    ),
                })
            else:
                source_note = _tr(
                    "perfect_planner.stage3.source.founder",
                    default="founder line",
                ) if not get_parents(rotation["candidate"]) else _tr(
                    "perfect_planner.stage3.source.existing",
                    default="existing line",
                )
                rotated_projection = _offspring_projection(rotation["parent"], rotation["candidate"])
                rotated_bp = _pair_breakpoint_analysis(rotation["parent"], rotation["candidate"], stimulation)
                stage3_import_counts.append(float(len(rotation["bring_stats"])))
                stage3_actions.append({
                    "action": _tr(
                        "perfect_planner.stage3.action.rotation_title",
                        default="Pair {index} Rotation",
                        index=idx,
                    ),
                    "target": (
                        f"{rotation['parent'].name} ({rotation['parent'].gender_display}) x "
                        f"{rotation['candidate'].name} ({rotation['candidate'].gender_display})"
                    ),
                    "target_grid": _planner_pair_grid(
                        rotation["parent"],
                        rotation["candidate"],
                        rotated_projection,
                    ),
                    "risk": rotation["risk"],
                    "why": (
                        _tr(
                            "perfect_planner.stage3.action.rotation_why",
                            default="Use this {source} outcross when Pair {index} stalls on {stats}. It covers missing 7-stats without falling back to family breeding. Projected 7+ coverage: {coverage:.1f}/7 at stimulation {stim}. Breakpoint: {headline}. ",
                            source=source_note,
                            index=idx,
                            stats=_fmt_stats(missing),
                            coverage=rotated_projection["seven_plus_total"],
                            stim=int(stimulation),
                            headline=rotated_bp["headline"],
                        )
                        + " ".join(rotated_bp["hints"][:2])
                    ),
                    "children": (
                        _tr(
                            "perfect_planner.stage3.action.rotation_children",
                            default="Promote the kitten that keeps the old locked stats while adding the missing stat coverage.",
                        )
                    ),
                    "rotate": (
                        _tr(
                            "perfect_planner.stage3.action.rotation_rotate",
                            default="Swap once the current line stops improving or when siblings would be your next obvious match.",
                        )
                    ),
                })

        stage_rows.append({
            "stage": _tr("perfect_planner.stage3.title", default="Stage 3"),
            "goal": _tr("perfect_planner.stage3.goal", default="Rotate partners before the line stalls"),
            "pairs": len(stage3_actions),
            "coverage": sum(stage3_import_counts) / max(1, len(stage3_import_counts)),
            "risk": max(
                [float(action["risk"]) for action in stage3_actions if action["risk"] is not None] or [0.0]
            ),
            "details": _tr(
                "perfect_planner.stage3.details",
                default="When and why to outcross instead of breeding inward",
            ),
            "summary": (
                _tr(
                    "perfect_planner.stage3.summary",
                    default="Rotate into unrelated or lower-risk partners when a line is missing specific 7s. This is the long-term maintenance step from issue #26.",
                )
            ),
            "notes": [
                _tr(
                    "perfect_planner.stage3.note.coverage",
                    default="Rotation advice is based on the missing 7-stat coverage from each foundation pair.",
                ),
                _tr(
                    "perfect_planner.stage3.note.wait_founder",
                    default="If no candidate appears, the roster is telling you to wait for a cleaner founder.",
                ),
            ],
            "actions": stage3_actions,
        })

        stage4_actions = []
        for idx, pair in enumerate(selected_pairs, 1):
            missing = pair["projection"]["missing_stats"]
            if missing:
                stage4_actions.append({
                    "action": _tr(
                        "perfect_planner.stage4.action.finish_title",
                        default="Finish Pair {index} through a keeper outcross",
                        index=idx,
                    ),
                    "target": _tr(
                        "perfect_planner.stage4.action.finish_target",
                        default="Finish: {stats}",
                        stats=_fmt_stats(missing),
                    ),
                    "risk": pair["risk"],
                    "why": (
                        _tr(
                            "perfect_planner.stage4.action.finish_why",
                            default="Once a keeper from this line is close to all 7s, use the Stage 3 rotation target rather than breeding back into siblings or parents.",
                        )
                    ),
                    "children": (
                        _tr(
                            "perfect_planner.stage4.action.finish_children",
                            default="Keep one opposite-sex backup in a different room so the finished line survives bad rolls.",
                        )
                    ),
                    "rotate": (
                        _tr(
                            "perfect_planner.stage4.action.finish_rotate",
                            default="Retire the weaker branch once the new keeper strictly improves the missing stats.",
                        )
                    ),
                })
            else:
                stage4_actions.append({
                    "action": _tr(
                        "perfect_planner.stage4.action.maintain_title",
                        default="Maintain Pair {index} as a clean 7-line",
                        index=idx,
                    ),
                    "target": _tr(
                        "perfect_planner.stage4.action.maintain_target",
                        default="All seven stats already covered",
                    ),
                    "risk": pair["risk"],
                    "why": (
                        _tr(
                            "perfect_planner.stage4.action.maintain_why",
                            default="This line already covers every target stat. The goal shifts from climbing to preserving.",
                        )
                    ),
                    "children": (
                        _tr(
                            "perfect_planner.stage4.action.maintain_children",
                            default="Keep a primary breeder and a backup of the opposite sex in separate rooms.",
                        )
                    ),
                    "rotate": (
                        _tr(
                            "perfect_planner.stage4.action.maintain_rotate",
                            default="Only bring in an unrelated stray if you need redundancy or the risk cap tightens.",
                        )
                    ),
                })

        stage_rows.append({
            "stage": _tr("perfect_planner.stage4.title", default="Stage 4"),
            "goal": _tr("perfect_planner.stage4.goal", default="Maintain and finish the strongest lines"),
            "pairs": len(stage4_actions),
            "coverage": sum(len(pair["projection"]["reachable_stats"]) for pair in selected_pairs) / len(selected_pairs),
            "risk": max(pair["risk"] for pair in selected_pairs),
            "details": _tr(
                "perfect_planner.stage4.details",
                default="How to convert the strongest keeper lines into durable perfect cats",
            ),
            "summary": (
                _tr(
                    "perfect_planner.stage4.summary",
                    default="Use the strongest keepers to finish the line, then preserve it with unrelated backups instead of letting the plan collapse into inbred maintenance.",
                )
            ),
            "notes": [
                _tr(
                    "perfect_planner.stage4.note.goal",
                    default="The planner is optimizing toward perfect 7-base-stat coverage, not short-term room fill.",
                ),
                _tr(
                    "perfect_planner.stage4.note.risk_zero",
                    default="Set Max inbreeding risk to 0 for the cleanest possible plan.",
                ),
            ],
            "actions": stage4_actions,
        })

        self._table.setRowCount(0)
        self._details_pane.show_stage(None)

        for row_idx, stage in enumerate(stage_rows):
            self._table.insertRow(row_idx)
            stage_item = QTableWidgetItem(stage["stage"])
            stage_item.setData(Qt.UserRole, stage)
            stage_item.setTextAlignment(Qt.AlignCenter)

            goal_item = QTableWidgetItem(stage["goal"])
            pair_item = QTableWidgetItem(str(stage["pairs"]))
            pair_item.setTextAlignment(Qt.AlignCenter)

            coverage_value = float(stage["coverage"])
            coverage_item = QTableWidgetItem(f"{coverage_value:.1f}/7")
            coverage_item.setTextAlignment(Qt.AlignCenter)
            if coverage_value >= 6.0:
                coverage_item.setForeground(QBrush(QColor(98, 194, 135)))
            elif coverage_value >= 4.5:
                coverage_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                coverage_item.setForeground(QBrush(QColor(190, 145, 40)))

            risk_value = float(stage["risk"])
            risk_item = QTableWidgetItem(f"{risk_value:.0f}%")
            risk_item.setTextAlignment(Qt.AlignCenter)
            if risk_value >= 20:
                risk_item.setForeground(QBrush(QColor(217, 119, 119)))
            elif risk_value > 0:
                risk_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                risk_item.setForeground(QBrush(QColor(98, 194, 135)))

            details_item = QTableWidgetItem(stage["details"])

            self._table.setItem(row_idx, 0, stage_item)
            self._table.setItem(row_idx, 1, goal_item)
            self._table.setItem(row_idx, 2, pair_item)
            self._table.setItem(row_idx, 3, coverage_item)
            self._table.setItem(row_idx, 4, risk_item)
            self._table.setItem(row_idx, 5, details_item)

        if excluded_cats:
            row_idx = self._table.rowCount()
            self._table.insertRow(row_idx)
            stage_item = QTableWidgetItem(_tr("room_optimizer.row.excluded", default="Excluded"))
            stage_item.setTextAlignment(Qt.AlignCenter)
            stage_item.setForeground(QBrush(QColor(170, 120, 120)))
            stage_item.setData(Qt.UserRole, {
                "stage": _tr("room_optimizer.row.excluded", default="Excluded"),
                "excluded_cat_rows": [
                    {
                        "name": f"{cat.name} ({cat.gender_display})",
                        "stats": dict(cat.base_stats),
                        "sum": _cat_base_sum(cat),
                        "traits": {
                            "aggression": _trait_display_label("aggression", cat.aggression) or _tr("common.unknown", default="unknown"),
                            "libido": _trait_display_label("libido", cat.libido) or _tr("common.unknown", default="unknown"),
                            "inbredness": _trait_display_label("inbredness", cat.inbredness) or _tr("common.unknown", default="unknown"),
                        },
                    }
                    for cat in excluded_cats
                ],
            })
            details_item = QTableWidgetItem(_tr("perfect_planner.summary.excluded_details", default="Excluded from Perfect 7 Planner calculations"))
            dash_pair = QTableWidgetItem("—"); dash_pair.setTextAlignment(Qt.AlignCenter)
            dash_cov = QTableWidgetItem("—"); dash_cov.setTextAlignment(Qt.AlignCenter)
            dash_risk = QTableWidgetItem("—"); dash_risk.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row_idx, 0, stage_item)
            self._table.setItem(row_idx, 1, QTableWidgetItem(_tr("room_optimizer.row.excluded_cats", default="{count} excluded cats", count=len(excluded_cats))))
            self._table.setItem(row_idx, 2, dash_pair)
            self._table.setItem(row_idx, 3, dash_cov)
            self._table.setItem(row_idx, 4, dash_risk)
            self._table.setItem(row_idx, 5, details_item)

        # Build cat locator data from all cats involved in the plan
        locator_cats: dict[int, dict] = {}  # keyed by db_key to deduplicate
        room_order_counter = 0
        for idx, pair in enumerate(selected_pairs):
            pair_label = _pair_stage_label(idx + 1)
            for cat in (pair["cat_a"], pair["cat_b"]):
                if cat.db_key not in locator_cats:
                    current = cat.room_display or STATUS_ABBREV.get(cat.status, cat.status) or _tr("common.unknown", default="?")
                    locator_cats[cat.db_key] = {
                        "name": cat.name,
                        "gender_display": cat.gender_display,
                        "db_key": cat.db_key,
                        "age": cat.age if cat.age is not None else cat.db_key,
                        "current_room": current,
                        "assigned_room": pair_label,
                        "room_order": room_order_counter,
                        "needs_move": False,
                    }
            room_order_counter += 1
        # Add rotation candidates
        for idx, pair in enumerate(selected_pairs):
            rotation = _rotation_candidate(pair)
            if rotation is not None:
                cat = rotation["candidate"]
                if cat.db_key not in locator_cats:
                    current = cat.room_display or STATUS_ABBREV.get(cat.status, cat.status) or _tr("common.unknown", default="?")
                    locator_cats[cat.db_key] = {
                        "name": cat.name,
                        "gender_display": cat.gender_display,
                        "db_key": cat.db_key,
                        "age": cat.age if cat.age is not None else cat.db_key,
                        "current_room": current,
                        "assigned_room": _rotation_stage_label(idx + 1),
                        "room_order": room_order_counter,
                        "needs_move": False,
                    }
                    room_order_counter += 1
        self._cat_locator.show_assignments(list(locator_cats.values()))

        if excluded_cats:
            self._summary.setText(
                _tr(
                    "perfect_planner.summary.planned_excluded",
                    default="Planned {pair_count} starting pairs from {cat_count} eligible cats ({excluded_count} excluded)",
                    pair_count=len(selected_pairs),
                    cat_count=len(alive_cats),
                    excluded_count=len(excluded_cats),
                )
            )
        else:
            self._summary.setText(
                _tr(
                    "perfect_planner.summary.planned",
                    default="Planned {pair_count} starting pairs from {cat_count} eligible cats",
                    pair_count=len(selected_pairs),
                    cat_count=len(alive_cats),
                )
            )

        if stage_rows:
            self._table.selectRow(0)
            self._details_pane.show_stage(stage_rows[0])


# ── Sidebar helpers ───────────────────────────────────────────────────────────


class CalibrationView(QWidget):
    """
    In-app calibration editor for parser-sensitive fields.
    Edits are saved to <save>.calibration.json and applied to app logic.
    """
    calibrationChanged = Signal()

    COL_NAME = 0
    COL_STATUS = 1
    COL_TOKEN = 2
    COL_TOKEN_FIELDS = 3
    COL_PARSED_G = 4
    COL_OVR_G = 5
    COL_DEFAULT_SEXUALITY = 6
    COL_OVR_SEXUALITY = 7
    COL_PARSED_AGE = 8
    COL_OVR_AGE = 9
    COL_PARSED_AGG = 10
    COL_OVR_AGG = 11
    COL_PARSED_LIB = 12
    COL_OVR_LIB = 13
    COL_PARSED_INB = 14
    COL_OVR_INB = 15
    COL_OVR_STR = 16
    COL_OVR_DEX = 17
    COL_OVR_CON = 18
    COL_OVR_INT = 19
    COL_OVR_SPD = 20
    COL_OVR_CHA = 21
    COL_OVR_LCK = 22

    class _AgeNumericDelegate(QStyledItemDelegate):
        def createEditor(self, parent, option, index):
            editor = QLineEdit(parent)
            # Allow blank (no override) or a non-negative number with up to 3 decimals.
            validator = QRegularExpressionValidator(
                QRegularExpression(r"^$|^\d+(?:\.\d{0,3})?$"),
                editor,
            )
            editor.setValidator(validator)
            return editor

    class _StatDelegate(QStyledItemDelegate):
        def createEditor(self, parent, option, index):
            editor = QLineEdit(parent)
            # Allow blank or integer 0-20
            validator = QRegularExpressionValidator(
                QRegularExpression(r"^$|^([0-9]|1[0-9]|20)$"),
                editor,
            )
            editor.setValidator(validator)
            return editor

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 10px; font-size:11px; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
            "QComboBox { background:#1a1a32; color:#ddd; border:1px solid #2a2a4a; padding:2px 6px; }"
            "QComboBox QAbstractItemView { background:#101023; color:#ddd; selection-background-color:#252545; }"
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; }"
            "QHeaderView::section { background:#151532; color:#7d8bb0; border:none; padding:4px; font-weight:bold; }"
        )
        self._save_path: Optional[str] = None
        self._cats: list[Cat] = []
        self._row_cat: list[Cat] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._title_label = QLabel()
        self._title_label.setStyleSheet("color:#ddd; font-size:18px; font-weight:bold;")
        root.addWidget(self._title_label)

        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet("color:#8d8da8; font-size:11px;")
        root.addWidget(self._desc_label)

        actions = QHBoxLayout()
        self._save_btn = QPushButton()
        self._reload_btn = QPushButton()
        self._export_btn = QPushButton()
        self._import_btn = QPushButton()
        self._status = QLabel("")
        self._status.setStyleSheet("color:#8d8da8; font-size:11px;")
        actions.addWidget(self._save_btn)
        actions.addWidget(self._reload_btn)
        actions.addWidget(self._export_btn)
        actions.addWidget(self._import_btn)
        actions.addSpacing(16)

        self._bulk_label = QLabel()
        self._bulk_label.setStyleSheet("color:#888; font-size:11px;")
        actions.addWidget(self._bulk_label)

        self._bulk_sexuality_combo = QComboBox()
        self._bulk_sexuality_combo.setFixedWidth(100)
        self._bulk_sexuality_combo.setStyleSheet(
            "QComboBox { background:#1a1a32; color:#ddd; border:1px solid #2a2a4a; padding:2px 6px; }"
            "QComboBox QAbstractItemView { background:#101023; color:#ddd; selection-background-color:#252545; }"
        )
        actions.addWidget(self._bulk_sexuality_combo)

        self._bulk_apply_btn = QPushButton()
        self._bulk_apply_btn.setStyleSheet(
            "QPushButton { background:#2a3a2a; color:#aaa; border:1px solid #3a5a3a; "
            "border-radius:4px; padding:4px 10px; font-size:10px; }"
            "QPushButton:hover { background:#3a4a3a; color:#ddd; }"
        )
        self._bulk_apply_btn.clicked.connect(self._on_bulk_apply_sexuality)
        actions.addWidget(self._bulk_apply_btn)

        actions.addStretch()
        actions.addWidget(self._status)
        root.addLayout(actions)

        self._table = QTableWidget(0, 23)
        self._table.setHorizontalHeaderLabels([
            "Name", "Status", "Gender\nToken", "Pre-G\nU32s", "Parsed\nG", "Override\nG",
            "Default\nSexuality", "Sexuality",
            "Parsed\nAge", "Override\nAge",
            "Parsed\nAgg", "Override\nAgg",
            "Parsed\nLibido", "Override\nLibido",
            "Parsed\nInbr", "Override\nInbr",
            "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.MultiSelection)
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self._table.setItemDelegateForColumn(self.COL_OVR_AGE, self._AgeNumericDelegate(self._table))
        for stat_col in (self.COL_OVR_STR, self.COL_OVR_DEX, self.COL_OVR_CON,
                         self.COL_OVR_INT, self.COL_OVR_SPD, self.COL_OVR_CHA, self.COL_OVR_LCK):
            self._table.setItemDelegateForColumn(stat_col, self._StatDelegate(self._table))
        hh = self._table.horizontalHeader()
        hh.setMinimumSectionSize(40)
        hh.setDefaultAlignment(Qt.AlignCenter)
        hh.setMinimumHeight(36)
        hh.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(self.COL_TOKEN, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(self.COL_TOKEN_FIELDS, QHeaderView.ResizeToContents)
        for col in (self.COL_PARSED_G, self.COL_OVR_G):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
            self._table.setColumnWidth(col, 68)
        hh.setSectionResizeMode(self.COL_DEFAULT_SEXUALITY, QHeaderView.Fixed)
        self._table.setColumnWidth(self.COL_DEFAULT_SEXUALITY, 80)
        hh.setSectionResizeMode(self.COL_OVR_SEXUALITY, QHeaderView.Fixed)
        self._table.setColumnWidth(self.COL_OVR_SEXUALITY, 80)
        for col in (
            self.COL_PARSED_AGE, self.COL_OVR_AGE,
            self.COL_PARSED_AGG, self.COL_OVR_AGG,
            self.COL_PARSED_LIB, self.COL_OVR_LIB,
            self.COL_PARSED_INB, self.COL_OVR_INB,
        ):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
            self._table.setColumnWidth(col, 76)
        for col in (self.COL_OVR_AGG, self.COL_OVR_LIB, self.COL_OVR_INB):
            self._table.setColumnWidth(col, 110)
        for stat_col in (self.COL_OVR_STR, self.COL_OVR_DEX, self.COL_OVR_CON,
                         self.COL_OVR_INT, self.COL_OVR_SPD, self.COL_OVR_CHA, self.COL_OVR_LCK):
            hh.setSectionResizeMode(stat_col, QHeaderView.Fixed)
            self._table.setColumnWidth(stat_col, 50)
        self._table.setSortingEnabled(True)
        root.addWidget(self._table, 1)

        self.retranslate_ui()
        self._save_btn.clicked.connect(self._save_clicked)
        self._reload_btn.clicked.connect(self._reload_clicked)
        self._export_btn.clicked.connect(self._export_clicked)
        self._import_btn.clicked.connect(self._import_clicked)

    def retranslate_ui(self):
        self._title_label.setText(_tr("calibration.title"))
        self._desc_label.setText(_tr("calibration.description"))
        self._save_btn.setText(_tr("calibration.save"))
        self._reload_btn.setText(_tr("calibration.reload"))
        self._export_btn.setText(_tr("calibration.export"))
        self._import_btn.setText(_tr("calibration.import"))
        self._bulk_label.setText(_tr("calibration.bulk_edit_selected"))
        self._table.setHorizontalHeaderLabels([
            _tr("table.column.name", default="Name"),
            _tr("table.column.status", default="Status"),
            _tr("calibration.column.gender_token", default="Gender\nToken"),
            _tr("calibration.column.pre_gender_u32s", default="Pre-G\nU32s"),
            _tr("calibration.column.parsed_gender", default="Parsed\nG"),
            _tr("calibration.column.override_gender", default="Override\nG"),
            _tr("calibration.column.default_sexuality", default="Default\nSexuality"),
            _tr("table.column.sexuality", default="Sexuality"),
            _tr("calibration.column.parsed_age", default="Parsed\nAge"),
            _tr("calibration.column.override_age", default="Override\nAge"),
            _tr("calibration.column.parsed_agg", default="Parsed\nAgg"),
            _tr("calibration.column.override_agg", default="Override\nAgg"),
            _tr("calibration.column.parsed_libido", default="Parsed\nLibido"),
            _tr("calibration.column.override_libido", default="Override\nLibido"),
            _tr("calibration.column.parsed_inbr", default="Parsed\nInbr"),
            _tr("calibration.column.override_inbr", default="Override\nInbr"),
            "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
        ])
        current_value = self._bulk_sexuality_combo.currentData()
        self._bulk_sexuality_combo.blockSignals(True)
        self._bulk_sexuality_combo.clear()
        self._bulk_sexuality_combo.addItem(_tr("calibration.sexuality.straight"), "straight")
        self._bulk_sexuality_combo.addItem(_tr("calibration.sexuality.gay"), "gay")
        self._bulk_sexuality_combo.addItem(_tr("calibration.sexuality.bi"), "bi")
        index = self._bulk_sexuality_combo.findData(current_value)
        if index >= 0:
            self._bulk_sexuality_combo.setCurrentIndex(index)
        self._bulk_sexuality_combo.blockSignals(False)
        self._bulk_apply_btn.setText(_tr("calibration.apply_sexuality"))
        if self._save_path and self._cats:
            self.set_context(self._save_path, self._cats)
        _enforce_min_font_in_widget_tree(self)

    @staticmethod
    def _fmt(v) -> str:
        if v is None:
            return ""
        try:
            return f"{float(v):.3f}".rstrip("0").rstrip(".")
        except Exception:
            return str(v)

    @staticmethod
    def _readonly_item(text: str) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        return it

    @staticmethod
    def _fmt_gender_token_fields(cat: Cat) -> str:
        vals = getattr(cat, "gender_token_fields", None)
        if not vals:
            return ""
        return ", ".join(str(int(v)) for v in vals)

    @staticmethod
    def _editable_item(text: str) -> QTableWidgetItem:
        return QTableWidgetItem(text)

    @staticmethod
    def _get_text_item(table: QTableWidget, row: int, col: int) -> str:
        w = table.cellWidget(row, col)
        if isinstance(w, QComboBox):
            data = w.currentData()
            if data is not None:
                return str(data).strip()
            return w.currentText().strip()
        it = table.item(row, col)
        return (it.text().strip() if it is not None else "")

    @staticmethod
    def _gender_combo(value: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem("", "")
        combo.addItem(_gender_display_label("male"), "male")
        combo.addItem(_gender_display_label("female"), "female")
        combo.addItem(_gender_display_label("?"), "?")
        idx = combo.findData((value or "").strip().lower(), Qt.MatchFixedString)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        return combo

    @staticmethod
    def _sexuality_combo(value: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem("", "")
        combo.addItem(_tr("calibration.sexuality.bi"), "bi")
        combo.addItem(_tr("calibration.sexuality.gay"), "gay")
        combo.addItem(_tr("calibration.sexuality.straight"), "straight")
        idx = combo.findData((value or "").strip().lower(), Qt.MatchFixedString)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        return combo

    @staticmethod
    def _trait_combo(field: str, options: tuple[str, ...], value: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem("", "")
        for option in options:
            combo.addItem(_trait_display_label(field, option) or option, option)
        idx = combo.findData((value or "").strip().lower(), Qt.MatchFixedString)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        return combo

    @staticmethod
    def _get_optional_float(table: QTableWidget, row: int, col: int):
        txt = CalibrationView._get_text_item(table, row, col)
        if txt == "":
            return None
        try:
            return float(txt)
        except Exception:
            return None

    def set_context(self, save_path: str, cats: list[Cat]):
        self._save_path = save_path
        self._cats = sorted([c for c in cats if c.status != "Gone"], key=lambda c: (c.name or "").lower())
        self._row_cat = []

        data = _load_calibration_data(save_path)
        overrides = data.get("overrides", {}) if isinstance(data, dict) else {}
        if not isinstance(overrides, dict):
            overrides = {}

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._cats))
        for row, cat in enumerate(self._cats):
            self._row_cat.append(cat)
            uid = (cat.unique_id or "").strip().lower()
            ov = overrides.get(uid) if isinstance(overrides.get(uid), dict) else {}

            name_item = self._readonly_item(cat.name or "?")
            name_item.setData(Qt.UserRole, cat)
            self._table.setItem(row, self.COL_NAME, name_item)
            self._table.setItem(row, self.COL_STATUS, self._readonly_item(STATUS_ABBREV.get(cat.status, cat.status)))
            self._table.setItem(row, self.COL_TOKEN, self._readonly_item(getattr(cat, "gender_token", "") or ""))
            self._table.setItem(row, self.COL_TOKEN_FIELDS, self._readonly_item(self._fmt_gender_token_fields(cat)))
            self._table.setItem(
                row,
                self.COL_PARSED_G,
                self._readonly_item(_gender_display_label(getattr(cat, "parsed_gender", cat.gender) or "?")),
            )
            self._table.setCellWidget(row, self.COL_OVR_G, self._gender_combo(str(ov.get("gender", "") or "")))
            self._table.setItem(row, self.COL_DEFAULT_SEXUALITY, self._readonly_item(_sexuality_display_label("straight")))
            self._table.setCellWidget(row, self.COL_OVR_SEXUALITY, self._sexuality_combo(str(ov.get("sexuality", "") or "")))

            self._table.setItem(row, self.COL_PARSED_AGE, self._readonly_item(self._fmt(getattr(cat, "parsed_age", None))))
            self._table.setItem(row, self.COL_OVR_AGE, self._editable_item(self._fmt(ov.get("age"))))
            self._table.setItem(row, self.COL_PARSED_AGG, self._readonly_item(self._fmt(getattr(cat, "parsed_aggression", None))))
            self._table.setCellWidget(
                row,
                self.COL_OVR_AGG,
                self._trait_combo("aggression", _CALIBRATION_TRAIT_OPTIONS["aggression"], _trait_label_from_value("aggression", ov.get("aggression"))),
            )
            self._table.setItem(row, self.COL_PARSED_LIB, self._readonly_item(self._fmt(getattr(cat, "parsed_libido", None))))
            self._table.setCellWidget(
                row,
                self.COL_OVR_LIB,
                self._trait_combo("libido", _CALIBRATION_TRAIT_OPTIONS["libido"], _trait_label_from_value("libido", ov.get("libido"))),
            )
            self._table.setItem(row, self.COL_PARSED_INB, self._readonly_item(self._fmt(getattr(cat, "parsed_inbredness", None))))
            self._table.setCellWidget(
                row,
                self.COL_OVR_INB,
                self._trait_combo("inbredness", _CALIBRATION_TRAIT_OPTIONS["inbredness"], _trait_label_from_value("inbredness", ov.get("inbredness"))),
            )

            # Add base stats override columns
            for i, stat_name in enumerate(STAT_NAMES):
                stat_col = self.COL_OVR_STR + i
                override_val = ov.get("base_stats", {}).get(stat_name, "")
                current_val = cat.base_stats.get(stat_name, 0)
                # Show current value in background, allow override
                item = self._editable_item(str(override_val) if override_val != "" else "")
                item.setToolTip(_tr("calibration.tooltip.current_stat", default="Current: {value}", value=current_val))
                self._table.setItem(row, stat_col, item)

        self._table.setSortingEnabled(True)
        self._status.setText(_tr("calibration.status.alive_cats", default="{count} alive cats", count=len(self._cats)))

    def _reload_clicked(self):
        if not self._save_path:
            self._status.setText(_tr("sidebar.no_save_loaded", default="No save loaded"))
            return
        self.set_context(self._save_path, self._cats)
        self._status.setText(_tr("calibration.status.reloaded", default="Reloaded calibration file"))

    def _collect_calibration_data(self) -> dict:
        overrides: dict[str, dict] = {}
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, self.COL_NAME)
            cat = name_item.data(Qt.UserRole) if name_item else None
            if cat is None:
                continue
            uid = (cat.unique_id or "").strip().lower()
            if not uid:
                continue

            g = _normalize_override_gender(self._get_text_item(self._table, row, self.COL_OVR_G))
            age = self._get_optional_float(self._table, row, self.COL_OVR_AGE)
            agg = _normalize_trait_override("aggression", self._get_text_item(self._table, row, self.COL_OVR_AGG))
            lib = _normalize_trait_override("libido", self._get_text_item(self._table, row, self.COL_OVR_LIB))
            inb = _normalize_trait_override("inbredness", self._get_text_item(self._table, row, self.COL_OVR_INB))
            sexuality_widget = self._table.cellWidget(row, self.COL_OVR_SEXUALITY)
            sexuality_raw = sexuality_widget.currentData() if isinstance(sexuality_widget, QComboBox) else ""
            sexuality = sexuality_raw if sexuality_raw in ("bi", "gay", "straight") else ""

            # Collect base stats overrides
            base_stats = {}
            for i, stat_name in enumerate(STAT_NAMES):
                stat_col = self.COL_OVR_STR + i
                txt = self._get_text_item(self._table, row, stat_col).strip()
                if txt:
                    try:
                        val = int(txt)
                        if 0 <= val <= 20:
                            base_stats[stat_name] = val
                    except ValueError:
                        pass

            if g or age is not None or agg or lib or inb or sexuality or base_stats:
                ov = {"name": cat.name}
                if g:
                    ov["gender"] = g
                if age is not None:
                    ov["age"] = age
                if agg:
                    ov["aggression"] = agg
                if lib:
                    ov["libido"] = lib
                if inb:
                    ov["inbredness"] = inb
                if sexuality:
                    ov["sexuality"] = sexuality
                if base_stats:
                    ov["base_stats"] = base_stats
                overrides[uid] = ov

        return {
            "version": 1,
            "overrides": overrides,
            "gender_token_map": _learn_gender_token_map(self._cats, overrides),
        }

    def _save_clicked(self):
        if not self._save_path:
            self._status.setText(_tr("sidebar.no_save_loaded", default="No save loaded"))
            return

        data = self._collect_calibration_data()
        overrides = data.get("overrides", {}) if isinstance(data, dict) else {}
        if not _save_calibration_data(self._save_path, data):
            self._status.setText(_tr("calibration.status.save_failed", default="Failed to save calibration"))
            return

        explicit, token_applied, _ = _apply_calibration_data(data, self._cats)
        self._status.setText(
            _tr(
                "calibration.status.saved",
                default="Saved. overrides={overrides} applied={applied} token-hints={hint_count}/{token_applied}",
                overrides=len(overrides),
                applied=explicit,
                hint_count=len(data["gender_token_map"]),
                token_applied=token_applied,
            )
        )
        self.calibrationChanged.emit()

    def _export_clicked(self):
        if not self._save_path:
            self._status.setText(_tr("sidebar.no_save_loaded", default="No save loaded"))
            return
        default_path = _calibration_path(self._save_path)
        path, _ = QFileDialog.getSaveFileName(
            self,
            _tr("calibration.export", default="Export Calibration"),
            default_path,
            _tr("calibration.file_filter", default="Calibration JSON (*.json);;All Files (*)"),
        )
        if not path:
            return
        data = self._collect_calibration_data()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=True)
            self._status.setText(_tr("calibration.status.exported", default="Exported calibration to {name}", name=os.path.basename(path)))
        except Exception:
            self._status.setText(_tr("calibration.status.export_failed", default="Failed to export calibration"))

    def _import_clicked(self):
        if not self._save_path:
            self._status.setText(_tr("sidebar.no_save_loaded", default="No save loaded"))
            return
        start = os.path.dirname(_calibration_path(self._save_path))
        path, _ = QFileDialog.getOpenFileName(
            self,
            _tr("calibration.import", default="Import Calibration"),
            start,
            _tr("calibration.file_filter", default="Calibration JSON (*.json);;All Files (*)"),
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self._status.setText(_tr("calibration.status.read_failed", default="Failed to read calibration file"))
            return
        if not isinstance(data, dict):
            self._status.setText(_tr("calibration.status.invalid_format", default="Invalid calibration format"))
            return
        overrides = data.get("overrides", {})
        if not isinstance(overrides, dict):
            overrides = {}
        token_map = data.get("gender_token_map", {})
        if not isinstance(token_map, dict):
            token_map = {}
        normalized = {
            "version": int(data.get("version", 1) or 1),
            "overrides": overrides,
            "gender_token_map": token_map or _learn_gender_token_map(self._cats, overrides),
        }
        if not _save_calibration_data(self._save_path, normalized):
            self._status.setText(_tr("calibration.status.import_failed", default="Failed to import calibration"))
            return
        explicit, token_applied, _ = _apply_calibration_data(normalized, self._cats)
        self.set_context(self._save_path, self._cats)
        self._status.setText(
            _tr(
                "calibration.status.imported",
                default="Imported. applied={applied} token={token_applied} from {name}",
                applied=explicit,
                token_applied=token_applied,
                name=os.path.basename(path),
            )
        )
        self.calibrationChanged.emit()

    def _on_bulk_apply_sexuality(self):
        """Apply sexuality to all selected rows."""
        selected_rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not selected_rows:
            self._status.setText(_tr("calibration.status.select_rows"))
            return

        sexuality = str(self._bulk_sexuality_combo.currentData() or "")
        for row in selected_rows:
            widget = self._table.cellWidget(row, self.COL_OVR_SEXUALITY)
            if isinstance(widget, QComboBox):
                idx = widget.findData(sexuality)
                widget.setCurrentIndex(idx if idx >= 0 else 0)

        self._save_clicked()
        self._status.setText(
            _tr(
                "calibration.status.applied",
                sexuality=_tr(f"calibration.sexuality.{sexuality}"),
                count=len(selected_rows),
            )
        )

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


# ── Mutation & Disorder Breeding Planner ──────────────────────────────────────

def _cat_has_trait(cat: 'Cat', category: str, trait_key: str) -> bool:
    """Check whether *cat* carries the given trait (mutation/passive/ability)."""
    if category == "mutation":
        return any(m.lower() == trait_key for m in (cat.mutations or []))
    elif category == "passive":
        return any(p.lower() == trait_key for p in (cat.passive_abilities or []))
    elif category == "ability":
        return any(a.lower() == trait_key for a in (cat.abilities or []))
    return False


class MutationDisorderPlannerView(QWidget):
    """View for planning breeding around specific mutations, disorders, and passives."""

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
        self._selected_pair: list[Cat] = []
        self._selected_traits: list[dict] = []  # [{category, key, display, weight}]
        self._navigate_to_cat_callback = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Header
        header = QHBoxLayout()
        self._title = QLabel()
        self._title.setStyleSheet("color:#ddd; font-size:18px; font-weight:bold;")
        header.addWidget(self._title)
        header.addStretch()
        root.addLayout(header)

        # Controls row
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self._room_label = QLabel()
        controls.addWidget(self._room_label)
        self._room_combo = QComboBox()
        self._room_combo.setFixedWidth(200)
        self._room_combo.setStyleSheet(
            "QComboBox { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        self._room_combo.currentIndexChanged.connect(self._refresh_table)
        controls.addWidget(self._room_combo)
        controls.addSpacing(16)
        self._stim_label = QLabel()
        controls.addWidget(self._stim_label)
        self._stim_spin = QSpinBox()
        self._stim_spin.setRange(0, 100)
        self._stim_spin.setValue(50)
        self._stim_spin.setFixedWidth(60)
        self._stim_spin.setStyleSheet(
            "QSpinBox { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px; }"
        )
        self._stim_spin.valueChanged.connect(self._on_stim_changed)
        controls.addWidget(self._stim_spin)
        controls.addStretch()
        self._pair_label = QLabel()
        self._pair_label.setStyleSheet("color:#666; font-size:11px;")
        controls.addWidget(self._pair_label)
        root.addLayout(controls)

        # Target trait row
        trait_row = QHBoxLayout()
        trait_row.setSpacing(8)
        self._target_trait_label = QLabel()
        trait_row.addWidget(self._target_trait_label)
        self._trait_search = QLineEdit()
        self._trait_search.setFixedWidth(160)
        self._trait_search.setClearButtonEnabled(True)
        self._trait_search.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        self._trait_search.textChanged.connect(self._on_trait_search_changed)
        trait_row.addWidget(self._trait_search)
        self._trait_combo = QComboBox()
        self._trait_combo.setFixedWidth(300)
        self._trait_combo.setStyleSheet(
            "QComboBox { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        self._trait_combo.currentIndexChanged.connect(self._on_target_trait_changed)
        trait_row.addWidget(self._trait_combo)
        # "Add" button to add selected trait to the multi-select list
        self._add_trait_btn = QPushButton()
        self._add_trait_btn.setFixedWidth(50)
        self._add_trait_btn.setStyleSheet(
            "QPushButton { background:#1f5f4a; color:#f2f7f3; border:1px solid #3f8f72; "
            "border-radius:4px; padding:4px 8px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#26735a; }"
        )
        self._add_trait_btn.clicked.connect(self._on_add_trait)
        trait_row.addWidget(self._add_trait_btn)
        # Master list of (display_text, user_data) for filtering
        self._trait_items_master: list[tuple[str, object]] = []
        self._trait_info_label = QLabel("")
        self._trait_info_label.setStyleSheet("color:#666; font-size:11px;")
        trait_row.addWidget(self._trait_info_label)
        trait_row.addStretch()
        root.addLayout(trait_row)

        # Main splitter: cat table left, outcome panel right
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background:#26264a; width:3px; }")

        # Left: cat table
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        self._cat_table = QTableWidget(0, 7)
        self._cat_table.setHorizontalHeaderLabels([
            "Name", "Gender", "Age", "Sum", "Mutations", "Passives / Disorders", "Abilities",
        ])
        self._cat_table.verticalHeader().setVisible(False)
        self._cat_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._cat_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._cat_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._cat_table.setSortingEnabled(True)
        self._cat_table.setAlternatingRowColors(True)
        hh = self._cat_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        self._cat_table.setColumnWidth(0, 130)
        self._cat_table.setColumnWidth(1, 50)
        self._cat_table.setColumnWidth(2, 40)
        self._cat_table.setColumnWidth(3, 50)
        self._cat_table.sortByColumn(0, Qt.AscendingOrder)
        self._cat_table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self._cat_table)
        splitter.addWidget(left)

        # Right: vertical splitter with selected traits (top) + outcome (bottom)
        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.setStyleSheet("QSplitter::handle { background:#26264a; height:3px; }")

        # -- Selected traits panel --
        traits_panel = QWidget()
        traits_panel.setStyleSheet("QWidget { background:#0e0e20; }")
        traits_panel_layout = QVBoxLayout(traits_panel)
        traits_panel_layout.setContentsMargins(8, 6, 8, 6)
        traits_panel_layout.setSpacing(4)
        traits_header = QHBoxLayout()
        self._traits_title = QLabel()
        self._traits_title.setStyleSheet("color:#8fb8a0; font-size:12px; font-weight:bold;")
        traits_header.addWidget(self._traits_title)
        traits_header.addStretch()
        self._clear_traits_btn = QPushButton()
        self._clear_traits_btn.setFixedHeight(22)
        self._clear_traits_btn.setStyleSheet(
            "QPushButton { background:#2a1a1a; color:#c88; border:1px solid #4a2a2a; "
            "border-radius:3px; padding:2px 8px; font-size:10px; }"
            "QPushButton:hover { background:#3a2a2a; }"
        )
        self._clear_traits_btn.clicked.connect(self._on_clear_all_traits)
        traits_header.addWidget(self._clear_traits_btn)
        self._find_pairs_btn = QPushButton()
        self._find_pairs_btn.setFixedHeight(22)
        self._find_pairs_btn.setStyleSheet(
            "QPushButton { background:#1f5f4a; color:#f2f7f3; border:1px solid #3f8f72; "
            "border-radius:3px; padding:2px 8px; font-size:10px; font-weight:bold; }"
            "QPushButton:hover { background:#26735a; }"
        )
        self._find_pairs_btn.clicked.connect(self._on_find_best_pairs)
        traits_header.addWidget(self._find_pairs_btn)
        traits_panel_layout.addLayout(traits_header)
        # Scroll area for trait rows
        self._traits_list_widget = QWidget()
        self._traits_list_layout = QVBoxLayout(self._traits_list_widget)
        self._traits_list_layout.setContentsMargins(0, 0, 0, 0)
        self._traits_list_layout.setSpacing(2)
        self._traits_list_layout.addStretch()
        traits_scroll = QScrollArea()
        traits_scroll.setWidgetResizable(True)
        traits_scroll.setFrameShape(QFrame.NoFrame)
        traits_scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        traits_scroll.setWidget(self._traits_list_widget)
        traits_scroll.setMaximumHeight(200)
        traits_panel_layout.addWidget(traits_scroll)
        self._traits_empty_label = QLabel()
        self._traits_empty_label.setStyleSheet("color:#555; font-size:10px;")
        self._traits_empty_label.setWordWrap(True)
        traits_panel_layout.addWidget(self._traits_empty_label)
        right_splitter.addWidget(traits_panel)

        # -- Outcome panel --
        self._outcome_scroll = QScrollArea()
        self._outcome_scroll.setWidgetResizable(True)
        self._outcome_scroll.setFrameShape(QFrame.NoFrame)
        self._outcome_scroll.setStyleSheet("QScrollArea { border:none; background:#0a0a18; }")
        self._outcome_widget = QWidget()
        self._outcome_layout = QVBoxLayout(self._outcome_widget)
        self._outcome_layout.setContentsMargins(12, 8, 12, 8)
        self._outcome_layout.setSpacing(6)
        self._outcome_placeholder = QLabel()
        self._outcome_placeholder.setStyleSheet("color:#555; font-size:12px;")
        self._outcome_placeholder.setWordWrap(True)
        self._outcome_layout.addWidget(self._outcome_placeholder)
        self._outcome_layout.addStretch()
        self._outcome_scroll.setWidget(self._outcome_widget)
        right_splitter.addWidget(self._outcome_scroll)

        right_splitter.setSizes([180, 420])
        splitter.addWidget(right_splitter)

        splitter.setSizes([500, 500])
        root.addWidget(splitter, 1)
        self.retranslate_ui()

    def retranslate_ui(self):
        self._title.setText(_tr("mutation_planner.title", default="Mutation & Disorder Breeding Planner"))
        self._room_label.setText(_tr("common.room", default="Room:"))
        self._stim_label.setText(_tr("detail.label.stimulation", default="Stimulation:"))
        self._target_trait_label.setText(_tr("mutation_planner.target_trait", default="Target Trait:"))
        self._trait_search.setPlaceholderText(_tr("mutation_planner.trait_search", default="Type to filter..."))
        self._add_trait_btn.setText(_tr("common.add", default="Add"))
        self._traits_title.setText(_tr("mutation_planner.selected_traits", default="Selected Traits"))
        self._clear_traits_btn.setText(_tr("mutation_planner.clear_all", default="Clear All"))
        self._find_pairs_btn.setText(_tr("mutation_planner.find_best_pairs", default="Find Best Pairs"))
        self._traits_empty_label.setText(_tr("mutation_planner.no_traits_selected", default="No traits selected. Use the combo box above and click Add."))
        self._cat_table.setHorizontalHeaderLabels([
            _tr("table.column.name", default="Name"),
            _tr("common.gender_text", default="Gender"),
            _tr("table.column.age", default="Age"),
            _tr("table.column.sum", default="Sum"),
            _tr("table.column.mutations", default="Mutations"),
            _tr("mutation_planner.table.passives_disorders", default="Passives / Disorders"),
            _tr("table.column.abilities", default="Abilities"),
        ])
        if self._selected_traits:
            self._rebuild_traits_list()
        if len(self._selected_pair) == 2:
            self._set_pair_label_pair(self._selected_pair[0], self._selected_pair[1])
        elif len(self._selected_pair) == 1:
            self._set_pair_label_selected(self._selected_pair[0])
        else:
            self._set_pair_label_default()
        if self._trait_combo.currentData() is not None:
            self._update_trait_plan(self._trait_combo.currentData())
        elif self._selected_traits:
            self._update_multi_trait_plan()
        elif len(self._selected_pair) == 2:
            self._update_outcome_panel(self._selected_pair[0], self._selected_pair[1])
        elif hasattr(self, "_outcome_placeholder") and self._outcome_layout.count():
            self._clear_outcome_panel()

    def _set_pair_label_default(self):
        self._pair_label.setText(_tr(
            "mutation_planner.pair_label.default",
            default="Ctrl+click two cats to compare breeding outcomes",
        ))
        self._pair_label.setStyleSheet("color:#666; font-size:11px;")

    def _set_pair_label_selected(self, cat: Cat):
        self._pair_label.setText(_tr(
            "mutation_planner.pair_label.selected_one",
            default="Selected: {name} -- select one more",
            name=cat.name,
        ))
        self._pair_label.setStyleSheet("color:#aa8; font-size:11px;")

    def _set_pair_label_pair(self, cat_a: Cat, cat_b: Cat):
        self._pair_label.setText(_tr(
            "mutation_planner.pair_label.selected_pair",
            default="Pair: {a} × {b}",
            a=cat_a.name,
            b=cat_b.name,
        ))
        self._pair_label.setStyleSheet("color:#8fb8a0; font-size:11px; font-weight:bold;")

    def set_cats(self, cats: list[Cat]):
        self._cats = cats
        self._selected_pair.clear()
        self._populate_room_filter()
        self._populate_trait_combo()
        self._refresh_table()

    def _populate_room_filter(self):
        self._room_combo.blockSignals(True)
        self._room_combo.clear()
        self._room_combo.addItem(_tr("mutation_planner.all_rooms", default="All Rooms"), "")
        rooms: dict[str, str] = {}
        for cat in self._cats:
            if cat.status == "Gone" or not cat.room or cat.room == "Adventure":
                continue
            if cat.room not in rooms:
                rooms[cat.room] = ROOM_DISPLAY.get(cat.room, cat.room)
        for raw, display in sorted(rooms.items(), key=lambda kv: kv[1]):
            self._room_combo.addItem(display, raw)
        self._room_combo.blockSignals(False)

    def _populate_trait_combo(self):
        prev = self._trait_combo.currentData()

        # Collect all traits across all alive cats, grouped by category
        mutations: dict[str, str] = {}   # raw -> display
        passives: dict[str, str] = {}
        abilities: dict[str, str] = {}

        for cat in self._cats:
            if cat.status == "Gone":
                continue
            for m in (cat.mutations or []):
                key = m.lower()
                if key not in mutations:
                    mutations[key] = _mutation_display_name(m)
            for p in (cat.passive_abilities or []):
                key = p.lower()
                if key not in passives:
                    passives[key] = _mutation_display_name(p)
            for a in (cat.abilities or []):
                key = a.lower()
                if key not in abilities:
                    abilities[key] = _mutation_display_name(a)

        # Build master list: (display_text, user_data)
        self._trait_items_master = []
        for key in sorted(mutations, key=lambda k: mutations[k]):
            self._trait_items_master.append(
                (_tr("mutation_planner.category.mutation", default="[Mutation] {name}", name=mutations[key]), ("mutation", key))
            )
        for key in sorted(passives, key=lambda k: passives[k]):
            self._trait_items_master.append(
                (_tr("mutation_planner.category.passive", default="[Passive/Disorder] {name}", name=passives[key]), ("passive", key))
            )
        for key in sorted(abilities, key=lambda k: abilities[k]):
            self._trait_items_master.append(
                (_tr("mutation_planner.category.ability", default="[Ability] {name}", name=abilities[key]), ("ability", key))
            )

        self._trait_search.clear()
        self._apply_trait_filter("", prev)

    def _on_trait_search_changed(self, text: str):
        prev = self._trait_combo.currentData()
        self._apply_trait_filter(text, prev)

    def _apply_trait_filter(self, search: str, restore_data=None):
        self._trait_combo.blockSignals(True)
        self._trait_combo.clear()
        self._trait_combo.addItem(_tr("mutation_planner.no_trait", default="(none -- select a trait to plan for)"), None)

        needle = search.strip().lower()
        last_category = None
        for display_text, user_data in self._trait_items_master:
            if needle and needle not in display_text.lower():
                continue
            # Insert category separator when category changes
            category = user_data[0] if isinstance(user_data, tuple) else None
            if category != last_category:
                if last_category is not None:
                    self._trait_combo.insertSeparator(self._trait_combo.count())
                last_category = category
            self._trait_combo.addItem(display_text, user_data)

        # Restore previous selection if still present
        if restore_data is not None:
            for i in range(self._trait_combo.count()):
                if self._trait_combo.itemData(i) == restore_data:
                    self._trait_combo.setCurrentIndex(i)
                    break
        self._trait_combo.blockSignals(False)

    def _on_target_trait_changed(self):
        data = self._trait_combo.currentData()
        if data is None:
            self._trait_info_label.setText("")
            # If a pair is selected, show pair outcome; otherwise show placeholder
            if len(self._selected_pair) == 2:
                self._update_outcome_panel(self._selected_pair[0], self._selected_pair[1])
            else:
                self._clear_outcome_panel()
            return
        # Clear cat table selection so the two modes don't conflict
        self._cat_table.clearSelection()
        self._selected_pair.clear()
        self._set_pair_label_default()
        self._update_trait_plan(data)

    # ── Multi-select trait management ──

    def _on_add_trait(self):
        """Add the currently selected trait from the combo to the selected list."""
        data = self._trait_combo.currentData()
        if data is None:
            return
        category, key = data
        # Check for duplicates
        if any(t["category"] == category and t["key"] == key for t in self._selected_traits):
            return
        display = self._trait_combo.currentText()
        self._selected_traits.append({
            "category": category, "key": key, "display": display, "weight": 5,
        })
        self._rebuild_traits_list()

    def _on_clear_all_traits(self):
        self._selected_traits.clear()
        self._rebuild_traits_list()
        self._clear_outcome_panel()

    def _on_remove_trait(self, index: int):
        if 0 <= index < len(self._selected_traits):
            self._selected_traits.pop(index)
            self._rebuild_traits_list()

    def _on_trait_weight_changed(self, index: int, value: int):
        if 0 <= index < len(self._selected_traits):
            self._selected_traits[index]["weight"] = value

    def _rebuild_traits_list(self):
        """Rebuild the selected traits list UI."""
        layout = self._traits_list_layout
        # Clear all widgets except the stretch at the end
        while layout.count() > 1:
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self._traits_empty_label.setVisible(len(self._selected_traits) == 0)

        for i, trait in enumerate(self._selected_traits):
            row = QWidget()
            row.setStyleSheet("QWidget { background:#151530; border-radius:3px; }")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(6, 2, 4, 2)
            row_layout.setSpacing(6)

            lbl = QLabel(trait["display"])
            lbl.setStyleSheet("color:#ccc; font-size:10px;")
            lbl.setMinimumWidth(100)
            row_layout.addWidget(lbl, 1)

            wt_label = QLabel(_tr("mutation_planner.weight", default="Wt:"))
            wt_label.setStyleSheet("color:#888; font-size:10px;")
            row_layout.addWidget(wt_label)

            spin = QSpinBox()
            spin.setRange(-10, 10)
            spin.setValue(trait["weight"])
            spin.setFixedWidth(45)

            def _spin_style(v):
                if v < 0:
                    return ("QSpinBox { background:#0d0d1c; color:#c86060; border:1px solid #2a2a4a;"
                            " border-radius:3px; padding:1px; font-size:10px; }")
                return ("QSpinBox { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
                        " border-radius:3px; padding:1px; font-size:10px; }")

            spin.setStyleSheet(_spin_style(trait["weight"]))
            idx = i  # capture for lambda
            spin.valueChanged.connect(lambda v, ii=idx, s=spin: (
                self._on_trait_weight_changed(ii, v),
                s.setStyleSheet(
                    "QSpinBox { background:#0d0d1c; color:#c86060; border:1px solid #2a2a4a;"
                    " border-radius:3px; padding:1px; font-size:10px; }" if v < 0
                    else "QSpinBox { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
                    " border-radius:3px; padding:1px; font-size:10px; }"
                )
            ))
            row_layout.addWidget(spin)

            remove_btn = QPushButton(_tr("common.remove_short", default="X"))
            remove_btn.setFixedSize(20, 20)
            remove_btn.setStyleSheet(
                "QPushButton { background:#2a1a1a; color:#c88; border:none; "
                "border-radius:3px; font-size:10px; font-weight:bold; }"
                "QPushButton:hover { background:#3a2a2a; }"
            )
            remove_btn.clicked.connect(lambda _, ii=idx: self._on_remove_trait(ii))
            row_layout.addWidget(remove_btn)

            layout.insertWidget(layout.count() - 1, row)  # insert before stretch

    def _on_find_best_pairs(self):
        """Find the best breeding pairs to cover all selected traits."""
        if not self._selected_traits:
            return
        self._cat_table.clearSelection()
        self._selected_pair.clear()
        self._set_pair_label_default()
        self._trait_combo.blockSignals(True)
        self._trait_combo.setCurrentIndex(0)
        self._trait_combo.blockSignals(False)
        self._trait_info_label.setText("")
        self._update_multi_trait_plan()

    def _update_multi_trait_plan(self):
        """Show breeding plan for multiple selected traits with weights."""
        stim = self._stim_spin.value()
        traits = self._selected_traits

        # Get all alive cats, excluding blacklisted
        alive = [c for c in self._cats if c.status != "Gone" and not c.is_blacklisted]

        # Score each cat: how many of the selected traits does it carry?
        def _cat_score(cat):
            return sum(t["weight"] for t in traits if _cat_has_trait(cat, t["category"], t["key"]))

        # Generate all candidate pairs via can_breed (respects sexuality overrides)
        candidate_pairs = []
        for i, a in enumerate(alive):
            for b in alive[i + 1:]:
                ok, _ = can_breed(a, b)
                if ok:
                    candidate_pairs.append((a, b))

        max_possible = sum(t["weight"] for t in traits if t["weight"] > 0)
        # With both-parents bonus: max is weight * 1.5 per positive trait
        max_score_with_bonus = max_possible * 1.5

        scored_pairs: list[tuple] = []
        for a, b in candidate_pairs:
            score = 0.0
            covered = []      # positive-weight traits covered by at least one parent
            uncovered = []    # positive-weight traits not covered
            penalized = []    # negative-weight traits carried by at least one parent
            for t in traits:
                a_has = _cat_has_trait(a, t["category"], t["key"])
                b_has = _cat_has_trait(b, t["category"], t["key"])
                w = t["weight"]
                if w < 0:
                    if a_has or b_has:
                        score += w  # penalty
                        if a_has and b_has:
                            score += w * 0.5  # extra penalty if both carry it
                        penalized.append(t)
                else:
                    if a_has or b_has:
                        score += w
                        if a_has and b_has:
                            score += w * 0.5  # bonus for both carriers
                        covered.append(t)
                    else:
                        uncovered.append(t)
            if covered:  # only show pairs that cover at least one positive trait
                inbred_a = a.inbredness if a.inbredness is not None else 0.0
                inbred_b = b.inbredness if b.inbredness is not None else 0.0
                avg_inbred = (inbred_a + inbred_b) / 2.0
                scored_pairs.append((score, a, b, covered, uncovered, penalized, avg_inbred))

        scored_pairs.sort(key=lambda x: (-x[0], x[6]))  # best score, lowest inbreeding

        # Build outcome panel
        layout = self._outcome_layout
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        layout.addWidget(self._sec_label(
            _tr(
                "mutation_planner.multi_trait.title",
                default="Multi-Trait Breeding Plan ({count} traits, max weight {weight})",
                count=len(traits),
                weight=max_possible,
            )
        ))

        if not scored_pairs:
            layout.addWidget(self._info_label(
                _tr(
                    "mutation_planner.no_pairs_for_traits",
                    default="No breeding pairs found that carry any of the selected traits.\nTry selecting different traits or checking that carriers exist.",
                )
            ))
            layout.addStretch()
            return

        # Check if any pair covers all positive traits
        pos_traits = [t for t in traits if t["weight"] > 0]
        best_score = scored_pairs[0][0]
        full_coverage = [p for p in scored_pairs if not p[4]]  # no uncovered positive traits

        if full_coverage:
            layout.addWidget(self._info_label(
                _tr(
                    "mutation_planner.multi_trait.full_coverage",
                    default="{count} pair(s) can cover ALL positive traits.",
                    count=len(full_coverage),
                )
            ))
        else:
            best_covered = len(scored_pairs[0][3])
            layout.addWidget(self._info_label(
                _tr(
                    "mutation_planner.multi_trait.partial_coverage",
                    default="No single pair covers all {total} positive traits.\nBest coverage: {covered}/{total} traits.",
                    total=len(pos_traits),
                    covered=best_covered,
                )
            ))

        # Show top pairs (limit to 20)
        layout.addWidget(self._sec_label(_tr("mutation_planner.multi_trait.best_pairs", default="Best Breeding Pairs")))
        show_pairs = scored_pairs[:20]

        pair_table = QTableWidget(len(show_pairs), 6)
        pair_table.setHorizontalHeaderLabels([
            _tr("mutation_planner.table.parent_a", default="Parent A"),
            _tr("mutation_planner.table.parent_b", default="Parent B"),
            _tr("mutation_planner.table.score", default="Score"),
            _tr("common.coverage", default="Coverage"),
            _tr("mutation_planner.table.uncovered_penalized", default="Uncovered / Penalized"),
            _tr("table.column.inbred", default="Inbreeding"),
        ])
        pair_table.verticalHeader().setVisible(False)
        pair_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        pair_table.setSelectionMode(QAbstractItemView.SingleSelection)
        pair_table.setMaximumHeight(min(30 + len(show_pairs) * 26, 500))
        pair_table.setStyleSheet(
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; font-size:11px; }"
        )
        phh = pair_table.horizontalHeader()
        phh.setSectionResizeMode(0, QHeaderView.Stretch)
        phh.setSectionResizeMode(1, QHeaderView.Stretch)
        phh.setSectionResizeMode(2, QHeaderView.Fixed)
        phh.setSectionResizeMode(3, QHeaderView.Stretch)
        phh.setSectionResizeMode(4, QHeaderView.Stretch)
        phh.setSectionResizeMode(5, QHeaderView.Fixed)
        pair_table.setColumnWidth(2, 55)
        pair_table.setColumnWidth(5, 70)
        pair_table.cellClicked.connect(self._on_pair_table_clicked)
        pair_table.setMouseTracking(True)
        pair_table.cellEntered.connect(lambda r, c: pair_table.setCursor(
            Qt.PointingHandCursor if c in (0, 1) else Qt.ArrowCursor
        ))

        for row, (score, a, b, covered, uncovered, penalized, avg_inbred) in enumerate(show_pairs):
            a_item = QTableWidgetItem(f"{a.name} ({a.gender_display})")
            a_item.setData(Qt.UserRole, a.db_key)
            a_item.setForeground(QColor("#5b9bd5"))
            a_item.setToolTip(_tr("common.tooltip.jump_to_alive", default="Click to jump to this cat in Alive Cats view"))
            pair_table.setItem(row, 0, a_item)

            b_item = QTableWidgetItem(f"{b.name} ({b.gender_display})")
            b_item.setData(Qt.UserRole, b.db_key)
            b_item.setForeground(QColor("#5b9bd5"))
            b_item.setToolTip(_tr("common.tooltip.jump_to_alive", default="Click to jump to this cat in Alive Cats view"))
            pair_table.setItem(row, 1, b_item)

            score_item = QTableWidgetItem(f"{score:.0f}/{max_possible:.0f}")
            score_item.setTextAlignment(Qt.AlignCenter)
            if score >= max_possible:
                score_item.setForeground(QColor("#8fb8a0"))
            elif score < 0:
                score_item.setForeground(QColor("#cc6666"))
            pair_table.setItem(row, 2, score_item)

            cov_names = ", ".join(t["display"].split("] ")[-1] for t in covered)
            pair_table.setItem(row, 3, QTableWidgetItem(cov_names))

            # Build uncovered + penalized cell
            parts = []
            if uncovered:
                parts.append(", ".join(t["display"].split("] ")[-1] for t in uncovered))
            if penalized:
                parts.append("\u26a0 " + ", ".join(t["display"].split("] ")[-1] for t in penalized))
            if parts:
                unc_item = QTableWidgetItem(" | ".join(parts))
                unc_item.setForeground(QColor("#cc8833") if penalized else QColor("#cc6666"))
                pair_table.setItem(row, 4, unc_item)
            else:
                full_item = QTableWidgetItem(_tr("mutation_planner.multi_trait.all_covered", default="All covered"))
                full_item.setForeground(QColor("#8fb8a0"))
                pair_table.setItem(row, 4, full_item)

            inbred_item = QTableWidgetItem(f"{avg_inbred:.0%}")
            inbred_item.setTextAlignment(Qt.AlignCenter)
            if avg_inbred > 0.2:
                inbred_item.setForeground(QColor("#cc6666"))
            pair_table.setItem(row, 5, inbred_item)

        layout.addWidget(pair_table)

        # Per-trait carrier summary
        layout.addWidget(self._sec_label(_tr("mutation_planner.multi_trait.carrier_summary", default="Trait Carrier Summary")))
        for t in traits:
            carriers = [c for c in alive if _cat_has_trait(c, t["category"], t["key"])]
            trait_short = t["display"].split("] ")[-1]
            w = t["weight"]
            if w < 0:
                prefix = "\u26a0 "
                color = "#cc8833" if carriers else "#888"
            else:
                prefix = ""
                color = "#8fb8a0" if carriers else "#cc6666"
            lbl = self._info_label(
                _tr(
                    "mutation_planner.multi_trait.carrier_line",
                    default="{prefix}{trait} (wt {weight}): {count} carrier(s)",
                    prefix=prefix,
                    trait=trait_short,
                    weight=w,
                    count=len(carriers),
                ) + (
                    _tr(
                        "mutation_planner.multi_trait.carrier_names_suffix",
                        default=" -- {names}",
                        names=", ".join(c.name for c in carriers[:8]),
                    )
                    if carriers else _tr("mutation_planner.multi_trait.none_suffix", default=" -- NONE")
                )
            )
            lbl.setStyleSheet(f"color:{color}; font-size:11px;")
            layout.addWidget(lbl)

        layout.addStretch()

    def _on_pair_table_clicked(self, row: int, col: int):
        """Navigate to a cat in the Alive Cats view when its name is clicked."""
        if col not in (0, 1):
            return
        table = self.sender()
        item = table.item(row, col)
        if item is None:
            return
        db_key = item.data(Qt.UserRole)
        if db_key is not None and self._navigate_to_cat_callback is not None:
            self._navigate_to_cat_callback(db_key)

    def get_selected_traits(self) -> list[dict]:
        """Return current selected traits with weights (for export to room optimizer)."""
        return [dict(t) for t in self._selected_traits]

    def _update_trait_plan(self, trait_data: tuple):
        """Show breeding plan for the selected target trait (single-trait mode)."""
        category, trait_key = trait_data
        stim = self._stim_spin.value()

        # Find all alive cats that have this trait, excluding blacklisted
        carriers: list[Cat] = []
        for cat in self._cats:
            if cat.status == "Gone" or cat.is_blacklisted:
                continue
            if _cat_has_trait(cat, category, trait_key):
                carriers.append(cat)

        # Display name for the trait
        trait_display = self._trait_combo.currentText()
        self._trait_info_label.setText(_tr(
            "mutation_planner.trait_info.carriers_found",
            default="{count} carrier(s) found",
            count=len(carriers),
        ))
        self._trait_info_label.setStyleSheet(
            f"color:{'#8fb8a0' if carriers else '#cc6666'}; font-size:11px;"
        )

        # Clear and rebuild outcome panel
        layout = self._outcome_layout
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        layout.addWidget(self._sec_label(_tr(
            "mutation_planner.single_trait.plan_title",
            default="Breeding Plan: {trait}",
            trait=trait_display,
        )))

        # ── Carriers ──
        layout.addWidget(self._sec_label(_tr(
            "mutation_planner.single_trait.carriers_title",
            default="Carriers ({count} cats)",
            count=len(carriers),
        )))
        if not carriers:
            layout.addWidget(self._info_label(_tr(
                "mutation_planner.single_trait.no_living_carriers",
                default="No living cats have this trait.",
            )))
            layout.addStretch()
            return

        carrier_table = QTableWidget(len(carriers), 4)
        carrier_table.setHorizontalHeaderLabels([
            _tr("table.column.name", default="Name"),
            _tr("common.gender_text", default="Gender"),
            _tr("table.column.age", default="Age"),
            _tr("table.column.room", default="Room"),
        ])
        carrier_table.verticalHeader().setVisible(False)
        carrier_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        carrier_table.setSelectionMode(QAbstractItemView.NoSelection)
        carrier_table.setMaximumHeight(min(30 + len(carriers) * 26, 250))
        carrier_table.setStyleSheet(
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; font-size:11px; }"
        )
        chh = carrier_table.horizontalHeader()
        chh.setSectionResizeMode(0, QHeaderView.Stretch)
        chh.setSectionResizeMode(1, QHeaderView.Fixed)
        chh.setSectionResizeMode(2, QHeaderView.Fixed)
        chh.setSectionResizeMode(3, QHeaderView.Stretch)
        carrier_table.setColumnWidth(1, 50)
        carrier_table.setColumnWidth(2, 40)
        for row, cat in enumerate(carriers):
            carrier_table.setItem(row, 0, QTableWidgetItem(cat.name))
            g_item = QTableWidgetItem(cat.gender_display if hasattr(cat, 'gender_display') else cat.gender)
            g_item.setTextAlignment(Qt.AlignCenter)
            carrier_table.setItem(row, 1, g_item)
            a_item = QTableWidgetItem(str(cat.age) if cat.age is not None else "-")
            a_item.setTextAlignment(Qt.AlignCenter)
            carrier_table.setItem(row, 2, a_item)
            room_name = ROOM_DISPLAY.get(cat.room, cat.room) if cat.room else "-"
            carrier_table.setItem(row, 3, QTableWidgetItem(room_name))
        layout.addWidget(carrier_table)

        # ── Inheritance mechanics ──
        layout.addWidget(self._sec_label(_tr("mutation_planner.single_trait.inheritance_mechanics", default="Inheritance Mechanics")))
        if category == "mutation":
            favor_weight = _stimulation_inheritance_weight(stim)
            layout.addWidget(self._info_label(
                _tr(
                    "mutation_planner.single_trait.inheritance_mutation",
                    default="Visual mutations: 80% chance all parts inherited. Mutated part favored at {percent:.1f}% (stim {stim}).\n\nStrategy: Breed a carrier with any cat. The offspring has a high chance of inheriting the mutated body part(s) from the carrier parent.",
                    percent=favor_weight * 100,
                    stim=stim,
                )
            ))
        elif category == "passive":
            passive_chance = 0.05 + 0.01 * stim
            layout.addWidget(self._info_label(
                _tr(
                    "mutation_planner.single_trait.inheritance_passive",
                    default="Passive/disorder inheritance: {percent:.1f}% chance per breeding (stim {stim}), 50/50 parent pick.\nIf both parents carry it: ~{percent:.1f}% chance, but either parent can pass it.\nDisorders specifically: 15% flat chance from each parent.\n\nStrategy: Pair two carriers together for the best odds. If only one carrier exists, breed them and hope for inheritance, then breed carriers from the next generation together.",
                    percent=min(passive_chance, 1.0) * 100,
                    stim=stim,
                )
            ))
        elif category == "ability":
            spell_chance = 0.2 + 0.025 * stim
            layout.addWidget(self._info_label(
                _tr(
                    "mutation_planner.single_trait.inheritance_ability",
                    default="Spell/ability inheritance: {percent:.1f}% chance per breeding (stim {stim}), 50/50 parent pick.\n\nStrategy: Breed a carrier with any cat. If both parents have the ability, either can pass it.",
                    percent=min(spell_chance, 1.0) * 100,
                    stim=stim,
                )
            ))

        # ── Recommended pairs ──
        layout.addWidget(self._sec_label(_tr("mutation_planner.single_trait.recommended_pairs", default="Recommended Breeding Pairs")))

        males = [c for c in carriers if c.gender and c.gender.upper() in ("M", "MALE")]
        females = [c for c in carriers if c.gender and c.gender.upper() in ("F", "FEMALE")]
        non_carriers = [c for c in self._cats if c.status != "Gone" and not c.is_blacklisted and c not in carriers]
        nc_males = [c for c in non_carriers if c.gender and c.gender.upper() in ("M", "MALE")]
        nc_females = [c for c in non_carriers if c.gender and c.gender.upper() in ("F", "FEMALE")]

        pairs: list[tuple[Cat, Cat, str]] = []  # (cat_a, cat_b, note)

        # Best: carrier x carrier (opposite gender)
        for m in males:
            for f in females:
                if m is f:
                    continue
                inbred_a = m.inbredness if m.inbredness is not None else 0.0
                inbred_b = f.inbredness if f.inbredness is not None else 0.0
                avg_inbred = (inbred_a + inbred_b) / 2.0
                note = _tr("mutation_planner.single_trait.note.both_carriers", default="Both carriers")
                if avg_inbred > 0.2:
                    note += _tr(
                        "mutation_planner.single_trait.note.inbreeding_risk",
                        default=" (inbreeding {percent:.0%} -- birth defect risk)",
                        percent=avg_inbred,
                    )
                pairs.append((m, f, note))

        # Good: carrier x non-carrier (opposite gender)
        if len(pairs) < 10:
            for carrier in carriers:
                pool = nc_females if carrier.gender and carrier.gender.upper() in ("M", "MALE") else nc_males
                for partner in pool[:5]:  # limit to avoid huge lists
                    pairs.append((carrier, partner, _tr("mutation_planner.single_trait.note.one_carrier", default="One carrier")))
                    if len(pairs) >= 15:
                        break
                if len(pairs) >= 15:
                    break

        if not pairs:
            if len(carriers) == 1:
                layout.addWidget(self._info_label(
                    _tr(
                        "mutation_planner.single_trait.only_one_carrier",
                        default="Only one carrier ({name}). Breed with any opposite-gender cat and select this trait again after offspring are born to find new carriers.",
                        name=carriers[0].name,
                    )
                ))
            else:
                layout.addWidget(self._info_label(
                    _tr(
                        "mutation_planner.single_trait.no_pairs_available",
                        default="No opposite-gender pairs found among carriers or available cats.",
                    )
                ))
        else:
            pair_table = QTableWidget(len(pairs), 4)
            pair_table.setHorizontalHeaderLabels([
                _tr("mutation_planner.table.parent_a", default="Parent A"),
                _tr("mutation_planner.table.parent_b", default="Parent B"),
                _tr("common.details", default="Details"),
                _tr("table.column.inbred", default="Inbreeding"),
            ])
            pair_table.verticalHeader().setVisible(False)
            pair_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            pair_table.setSelectionMode(QAbstractItemView.NoSelection)
            pair_table.setMaximumHeight(min(30 + len(pairs) * 26, 400))
            pair_table.setStyleSheet(
                "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; font-size:11px; }"
            )
            phh = pair_table.horizontalHeader()
            phh.setSectionResizeMode(0, QHeaderView.Stretch)
            phh.setSectionResizeMode(1, QHeaderView.Stretch)
            phh.setSectionResizeMode(2, QHeaderView.Stretch)
            phh.setSectionResizeMode(3, QHeaderView.Fixed)
            pair_table.setColumnWidth(3, 80)
            for row, (ca, cb, note) in enumerate(pairs):
                pair_table.setItem(row, 0, QTableWidgetItem(ca.name))
                pair_table.setItem(row, 1, QTableWidgetItem(cb.name))
                pair_table.setItem(row, 2, QTableWidgetItem(note))
                inbred_a = ca.inbredness if ca.inbredness is not None else 0.0
                inbred_b = cb.inbredness if cb.inbredness is not None else 0.0
                avg = (inbred_a + inbred_b) / 2.0
                inbred_item = QTableWidgetItem(f"{avg:.0%}")
                inbred_item.setTextAlignment(Qt.AlignCenter)
                if avg > 0.2:
                    inbred_item.setForeground(QColor("#cc6666"))
                pair_table.setItem(row, 3, inbred_item)
            layout.addWidget(pair_table)

        layout.addStretch()

    def _filtered_cats(self) -> list[Cat]:
        room_filter = self._room_combo.currentData() or ""
        result = []
        for cat in self._cats:
            if cat.status == "Gone":
                continue
            if room_filter and cat.room != room_filter:
                continue
            result.append(cat)
        return result

    def _refresh_table(self):
        self._cat_table.setSortingEnabled(False)
        cats = self._filtered_cats()
        self._cat_table.setRowCount(len(cats))
        for row, cat in enumerate(cats):
            name_item = QTableWidgetItem(cat.name)
            name_item.setData(Qt.UserRole, id(cat))
            self._cat_table.setItem(row, 0, name_item)

            gender_item = QTableWidgetItem(cat.gender_display if hasattr(cat, 'gender_display') else cat.gender)
            gender_item.setTextAlignment(Qt.AlignCenter)
            self._cat_table.setItem(row, 1, gender_item)

            age_item = _SortByUserRoleItem(str(cat.age) if cat.age is not None else "—")
            age_item.setData(Qt.UserRole, cat.age if cat.age is not None else -1)
            age_item.setTextAlignment(Qt.AlignCenter)
            self._cat_table.setItem(row, 2, age_item)

            stat_sum = sum(cat.base_stats.values()) if cat.base_stats else 0
            sum_item = _SortByUserRoleItem(str(stat_sum))
            sum_item.setData(Qt.UserRole, stat_sum)
            sum_item.setTextAlignment(Qt.AlignCenter)
            self._cat_table.setItem(row, 3, sum_item)

            muts = ", ".join(_mutation_display_name(m) for m in cat.mutations) if cat.mutations else "—"
            self._cat_table.setItem(row, 4, QTableWidgetItem(muts))

            passives = ", ".join(_mutation_display_name(p) for p in cat.passive_abilities) if cat.passive_abilities else "—"
            self._cat_table.setItem(row, 5, QTableWidgetItem(passives))

            abils = ", ".join(_mutation_display_name(a) for a in cat.abilities) if cat.abilities else "—"
            self._cat_table.setItem(row, 6, QTableWidgetItem(abils))
        self._cat_table.setSortingEnabled(True)

    def _on_stim_changed(self):
        trait_data = self._trait_combo.currentData()
        if trait_data is not None:
            self._update_trait_plan(trait_data)
        elif len(self._selected_pair) == 2:
            self._update_outcome_panel(self._selected_pair[0], self._selected_pair[1])

    def _on_selection_changed(self):
        rows = sorted(set(idx.row() for idx in self._cat_table.selectionModel().selectedRows()))
        cats_by_id = {id(c): c for c in self._cats}
        selected: list[Cat] = []
        for r in rows:
            item = self._cat_table.item(r, 0)
            if item is None:
                continue
            cat_id = item.data(Qt.UserRole)
            cat = cats_by_id.get(cat_id)
            if cat is not None:
                selected.append(cat)

        if len(selected) == 2:
            self._selected_pair = selected
            self._set_pair_label_pair(selected[0], selected[1])
            # Clear trait dropdown so pair view takes over
            self._trait_combo.blockSignals(True)
            self._trait_combo.setCurrentIndex(0)
            self._trait_combo.blockSignals(False)
            self._trait_info_label.setText("")
            self._update_outcome_panel(selected[0], selected[1])
        elif len(selected) == 1:
            self._selected_pair = selected
            self._set_pair_label_selected(selected[0])
            if self._trait_combo.currentData() is None:
                self._clear_outcome_panel()
        else:
            self._selected_pair.clear()
            self._set_pair_label_default()
            if self._trait_combo.currentData() is None:
                self._clear_outcome_panel()

    def _clear_outcome_panel(self):
        layout = self._outcome_layout
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._outcome_placeholder = QLabel(_tr(
            "mutation_planner.outcome_placeholder",
            default="Select two cats to see breeding outcome analysis.",
        ))
        self._outcome_placeholder.setStyleSheet("color:#555; font-size:12px;")
        self._outcome_placeholder.setWordWrap(True)
        layout.addWidget(self._outcome_placeholder)
        layout.addStretch()

    def _sec_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#7d8bb0; font-size:13px; font-weight:bold; padding:4px 0 2px 0;")
        return lbl

    def _info_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#bbb; font-size:11px;")
        lbl.setWordWrap(True)
        return lbl

    def _update_outcome_panel(self, cat_a: Cat, cat_b: Cat):
        layout = self._outcome_layout
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        stim = self._stim_spin.value()
        favor_weight = _stimulation_inheritance_weight(stim)

        # ── Header ──
        layout.addWidget(self._sec_label(
            f"{cat_a.name} \u00d7 {cat_b.name}"
        ))

        # ── Disorder Inheritance ──
        layout.addWidget(self._sec_label(_tr("mutation_planner.outcome.disorder_title", default="Disorder Inheritance")))
        layout.addWidget(self._info_label(
            _tr(
                "mutation_planner.outcome.disorder_intro",
                default="Each parent has a flat 15% chance per disorder to pass it down.",
            )
        ))

        a_disorders = cat_a.disorders or []
        b_disorders = cat_b.disorders or []

        disorder_rows: list[str] = []
        seen = set()
        for disorder in a_disorders:
            name = _mutation_display_name(disorder)
            key = disorder.lower()
            if key not in seen:
                seen.add(key)
                # Check if other parent also has it
                b_has = any(other.lower() == key for other in b_disorders)
                if b_has:
                    pct = 1.0 - (0.85 * 0.85)  # both parents: ~27.75%
                    disorder_rows.append(_tr(
                        "mutation_planner.outcome.disorder.both_parents",
                        default="  {name}: {percent:.1f}% (both parents)",
                        name=name,
                        percent=pct * 100,
                    ))
                else:
                    disorder_rows.append(_tr(
                        "mutation_planner.outcome.disorder.one_parent",
                        default="  {name}: 15% (from {parent})",
                        name=name,
                        parent=cat_a.name,
                    ))
        for disorder in b_disorders:
            key = disorder.lower()
            if key not in seen:
                seen.add(key)
                name = _mutation_display_name(disorder)
                disorder_rows.append(_tr(
                    "mutation_planner.outcome.disorder.one_parent",
                    default="  {name}: 15% (from {parent})",
                    name=name,
                    parent=cat_b.name,
                ))

        if disorder_rows:
            layout.addWidget(self._info_label("\n".join(disorder_rows)))
        else:
            layout.addWidget(self._info_label(_tr(
                "mutation_planner.outcome.disorder.none",
                default="  Neither parent has disorders to pass.",
            )))

        # Birth defect risk breakdown
        coi = kinship_coi(cat_a, cat_b)
        disorder_ch, part_defect_ch, combined_ch = _malady_breakdown(coi)
        inbred_note = ""
        if cat_a.inbredness is None and cat_b.inbredness is None:
            inbred_note = _tr(
                "mutation_planner.outcome.risk.unknown_parent_inbreeding",
                default=" (parent inbreeding unknown, assuming 0)",
            )
        layout.addWidget(self._info_label(
            _tr(
                "mutation_planner.outcome.risk.summary",
                default="  Disorder chance: {disorder:.1f}%  (base 2%, scales above 0.20 CoI)\n  Part defect chance: {defect:.1f}%  (0 below 0.05 CoI, then 1.5x CoI)\n  Combined risk: {combined:.1f}%{note}",
                disorder=disorder_ch * 100,
                defect=part_defect_ch * 100,
                combined=combined_ch * 100,
                note=inbred_note,
            )
        ))

        note_lbl = QLabel(
            _tr(
                "mutation_planner.outcome.disorder.note",
                default="Note: This view uses parsed disorder slots from the save file.\nPassive abilities are tracked separately and are not included\nin these disorder inheritance odds.",
            )
        )
        note_lbl.setStyleSheet("color:#665; font-size:10px; font-style:italic;")
        note_lbl.setWordWrap(True)
        layout.addWidget(note_lbl)

        # ── Visual Mutation Inheritance ──
        layout.addWidget(self._sec_label(_tr(
            "mutation_planner.outcome.mutation_title",
            default="Mutation Inheritance (Visual Parts)",
        )))
        layout.addWidget(self._info_label(
            _tr(
                "mutation_planner.outcome.mutation_intro",
                default="80% chance all parts are inherited. Stimulation {stim}: mutated parts favored at {percent:.1f}%.",
                stim=stim,
                percent=favor_weight * 100,
            )
        ))

        # Group mutations by group_key
        a_by_group: dict[str, list[dict]] = {}
        for entry in (cat_a.visual_mutation_entries or []):
            gk = entry.get("group_key", "")
            a_by_group.setdefault(gk, []).append(entry)
        b_by_group: dict[str, list[dict]] = {}
        for entry in (cat_b.visual_mutation_entries or []):
            gk = entry.get("group_key", "")
            b_by_group.setdefault(gk, []).append(entry)

        all_groups = sorted(set(list(a_by_group.keys()) + list(b_by_group.keys())))
        if all_groups:
            mut_table = QTableWidget(len(all_groups), 4)
            mut_table.setHorizontalHeaderLabels([
                _tr("mutation_planner.table.body_part", default="Body Part"),
                cat_a.name,
                cat_b.name,
                _tr("mutation_planner.table.odds", default="Odds"),
            ])
            mut_table.verticalHeader().setVisible(False)
            mut_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            mut_table.setSelectionMode(QAbstractItemView.NoSelection)
            mut_table.setMaximumHeight(min(30 + len(all_groups) * 26, 300))
            mut_table.setStyleSheet(
                "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; font-size:11px; }"
            )
            mhh = mut_table.horizontalHeader()
            mhh.setSectionResizeMode(0, QHeaderView.Interactive)
            mhh.setSectionResizeMode(1, QHeaderView.Stretch)
            mhh.setSectionResizeMode(2, QHeaderView.Stretch)
            mhh.setSectionResizeMode(3, QHeaderView.Interactive)
            mut_table.setColumnWidth(0, 100)
            mut_table.setColumnWidth(3, 120)

            for row, gk in enumerate(all_groups):
                a_entries = a_by_group.get(gk, [])
                b_entries = b_by_group.get(gk, [])
                part_label = a_entries[0].get("part_label", gk) if a_entries else (
                    b_entries[0].get("part_label", gk) if b_entries else gk
                )
                a_names = ", ".join(e.get("name", "?") for e in a_entries) or _tr("detail.appearance.base", default="Base")
                b_names = ", ".join(e.get("name", "?") for e in b_entries) or _tr("detail.appearance.base", default="Base")

                a_has_mutation = bool(a_entries)
                b_has_mutation = bool(b_entries)

                if a_has_mutation and b_has_mutation:
                    if a_names == b_names:
                        odds_text = _tr("mutation_planner.outcome.mutation.same", default="Same mutation")
                    else:
                        odds_text = _tr(
                            "mutation_planner.outcome.mutation.split",
                            default="{a}: 50% / {b}: 50%",
                            a=cat_a.name,
                            b=cat_b.name,
                        )
                elif a_has_mutation:
                    odds_text = _tr(
                        "mutation_planner.outcome.mutation.one_parent",
                        default="Mutated ({parent}): {percent:.0f}%",
                        parent=cat_a.name,
                        percent=favor_weight * 100,
                    )
                elif b_has_mutation:
                    odds_text = _tr(
                        "mutation_planner.outcome.mutation.one_parent",
                        default="Mutated ({parent}): {percent:.0f}%",
                        parent=cat_b.name,
                        percent=favor_weight * 100,
                    )
                else:
                    odds_text = _tr("mutation_planner.outcome.mutation.none", default="No mutations")

                mut_table.setItem(row, 0, QTableWidgetItem(part_label))
                mut_table.setItem(row, 1, QTableWidgetItem(a_names))
                mut_table.setItem(row, 2, QTableWidgetItem(b_names))
                mut_table.setItem(row, 3, QTableWidgetItem(odds_text))

            layout.addWidget(mut_table)
        else:
            layout.addWidget(self._info_label(_tr(
                "mutation_planner.outcome.mutation.no_visual",
                default="  No visual mutations on either parent.",
            )))

        # ── Passive Inheritance ──
        layout.addWidget(self._sec_label(_tr(
            "mutation_planner.outcome.passive_title",
            default="Passive / Ability Inheritance",
        )))
        passive_chance = 0.05 + 0.01 * stim
        spell_chance = 0.2 + 0.025 * stim
        a_passives = list(cat_a.passive_abilities or [])
        b_passives = list(cat_b.passive_abilities or [])
        layout.addWidget(self._info_label(
            _tr(
                "mutation_planner.outcome.passive_intro",
                default="Passive inheritance chance: {passive:.1f}% (50/50 parent pick)\nSpell inheritance chance: {spell:.1f}% (50/50 parent pick)",
                passive=min(passive_chance, 1.0) * 100,
                spell=min(spell_chance, 1.0) * 100,
            )
        ))

        if a_passives or b_passives:
            chips, _, _ = _inheritance_candidates(
                a_passives, b_passives, stim, _mutation_display_name,
            )
            passive_lines = []
            for label, tip in chips:
                passive_lines.append(f"  {label}")
            if passive_lines:
                layout.addWidget(self._info_label(
                    _tr(
                        "mutation_planner.outcome.passive_weighted",
                        default="If a passive IS inherited, weighted odds per entry:\n{lines}",
                        lines="\n".join(passive_lines),
                    )
                ))

        if cat_a.abilities or cat_b.abilities:
            spell_chips, _, _ = _inheritance_candidates(
                cat_a.abilities or [], cat_b.abilities or [],
                stim, _mutation_display_name,
            )
            spell_lines = []
            for label, tip in spell_chips:
                spell_lines.append(f"  {label}")
            if spell_lines:
                layout.addWidget(self._info_label(
                    _tr(
                        "mutation_planner.outcome.spell_weighted",
                        default="If a spell IS inherited, weighted odds per entry:\n{lines}",
                        lines="\n".join(spell_lines),
                    )
                ))

        # ── Stat Inheritance ──
        layout.addWidget(self._sec_label(_tr("mutation_planner.outcome.stat_title", default="Stat Inheritance")))
        layout.addWidget(self._info_label(
            _tr(
                "mutation_planner.outcome.stat_intro",
                default="Better stat favored at {percent:.1f}% (stim {stim}).",
                percent=favor_weight * 100,
                stim=stim,
            )
        ))

        stat_table = QTableWidget(7, 4)
        stat_table.setHorizontalHeaderLabels([
            _tr("mutation_planner.table.stat", default="Stat"),
            cat_a.name,
            cat_b.name,
            _tr("mutation_planner.table.offspring_likely", default="Offspring likely"),
        ])
        stat_table.verticalHeader().setVisible(False)
        stat_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        stat_table.setSelectionMode(QAbstractItemView.NoSelection)
        stat_table.setMaximumHeight(30 + 7 * 26)
        stat_table.setStyleSheet(
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; font-size:11px; }"
        )
        shh = stat_table.horizontalHeader()
        shh.setSectionResizeMode(0, QHeaderView.Fixed)
        shh.setSectionResizeMode(1, QHeaderView.Fixed)
        shh.setSectionResizeMode(2, QHeaderView.Fixed)
        shh.setSectionResizeMode(3, QHeaderView.Stretch)
        stat_table.setColumnWidth(0, 40)
        stat_table.setColumnWidth(1, 60)
        stat_table.setColumnWidth(2, 60)

        for row, stat_name in enumerate(STAT_NAMES):
            a_val = cat_a.base_stats.get(stat_name, 0)
            b_val = cat_b.base_stats.get(stat_name, 0)
            if a_val == b_val:
                likely = _tr(
                    "mutation_planner.outcome.stat.same",
                    default="{value} (same)",
                    value=a_val,
                )
            elif a_val > b_val:
                likely = _tr(
                    "mutation_planner.outcome.stat.split",
                    default="{high} ({high_pct:.0f}%) or {low} ({low_pct:.0f}%)",
                    high=a_val,
                    high_pct=favor_weight * 100,
                    low=b_val,
                    low_pct=(1 - favor_weight) * 100,
                )
            else:
                likely = _tr(
                    "mutation_planner.outcome.stat.split",
                    default="{high} ({high_pct:.0f}%) or {low} ({low_pct:.0f}%)",
                    high=b_val,
                    high_pct=favor_weight * 100,
                    low=a_val,
                    low_pct=(1 - favor_weight) * 100,
                )

            stat_table.setItem(row, 0, QTableWidgetItem(stat_name))
            a_item = QTableWidgetItem(str(a_val))
            a_item.setTextAlignment(Qt.AlignCenter)
            stat_table.setItem(row, 1, a_item)
            b_item = QTableWidgetItem(str(b_val))
            b_item.setTextAlignment(Qt.AlignCenter)
            stat_table.setItem(row, 2, b_item)
            stat_table.setItem(row, 3, QTableWidgetItem(likely))

        layout.addWidget(stat_table)

        # ── Lineage Info ──
        layout.addWidget(self._sec_label(_tr("detail.section.lineage", default="LINEAGE")))
        lineage_lines = []
        for label, cat in [(cat_a.name, cat_a), (cat_b.name, cat_b)]:
            pa_name = cat.parent_a.name if cat.parent_a else _tr("common.unknown", default="Unknown")
            pb_name = cat.parent_b.name if cat.parent_b else _tr("common.unknown", default="Unknown")
            inbred_str = f"{cat.inbredness:.2f}" if cat.inbredness is not None else "?"
            lineage_lines.append(_tr(
                "mutation_planner.outcome.lineage.parents",
                default="{name}: parents = {parent_a} × {parent_b}, inbreeding = {inbredness}",
                name=label,
                parent_a=pa_name,
                parent_b=pb_name,
                inbredness=inbred_str,
            ))

            # Show grandparent disorders if available
            for gp in (cat.parent_a, cat.parent_b):
                if gp is not None and gp.passive_abilities:
                    gp_passives = ", ".join(_mutation_display_name(p) for p in gp.passive_abilities)
                    lineage_lines.append(_tr(
                        "mutation_planner.outcome.lineage.passives",
                        default="    {name} passives: {passives}",
                        name=gp.name,
                        passives=gp_passives,
                    ))

        layout.addWidget(self._info_label("\n".join(lineage_lines)))

        layout.addStretch()


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    @staticmethod
    def _set_bulk_toggle_label(btn: QPushButton, label: str, enabled: bool):
        btn.setText(_tr("bulk.label_template", label=label, state=_tr("common.on" if enabled else "common.off")))

    def __init__(self, initial_save: Optional[str] = None):
        super().__init__()
        _set_current_language(_saved_language())
        _refresh_localized_constants()
        self.setWindowTitle(_tr("app.title"))
        self.resize(1440, 900)

        self._current_save = None
        self._cats: list[Cat] = []
        self._room_btns: dict = {}
        self._active_btn = None
        self._show_lineage: bool = False
        self._tree_view: Optional[FamilyTreeBrowserView] = None
        self._safe_breeding_view: Optional[SafeBreedingView] = None
        self._breeding_partners_view: Optional[BreedingPartnersView] = None
        self._room_optimizer_view: Optional[RoomOptimizerView] = None
        self._perfect_planner_view: Optional[PerfectCatPlannerView] = None
        self._calibration_view: Optional[CalibrationView] = None
        self._breeding_cache: Optional[BreedingCache] = None
        self._cache_worker: Optional[BreedingCacheWorker] = None
        self._save_load_worker: Optional[SaveLoadWorker] = None
        self._prev_parent_keys: dict[int, tuple] = {}
        self._zoom_percent: int = 100
        self._font_size_offset: int = 0   # pt offset applied on top of zoom
        self._base_font: QFont = QApplication.instance().font()
        self._base_sidebar_width = 190
        self._base_header_height = 46
        self._base_search_width = 180
        self._base_col_widths = {
            COL_NAME: 130,
            COL_GEN: _W_GEN,
            COL_STAT: _W_STATUS,
            COL_BL: 34,
            COL_MB: 34,
            COL_SUM: 38,
            COL_ABIL: 180,
            COL_MUTS: 155,
            COL_RELNS: _W_RELNS,
            COL_REL: _W_REL,
            COL_AGE: 34,
            COL_AGG: _W_TRAIT,
            COL_LIB: _W_TRAIT,
            COL_INBRD: _W_TRAIT,
            COL_SEXUALITY: _W_TRAIT,
            **{c: _W_STAT for c in STAT_COLS},
        }

        self._build_ui()
        self._build_menu()
        self._apply_zoom()

        # Progress bar for breeding cache computation
        self._cache_progress = QProgressBar()
        self._cache_progress.setFixedWidth(200)
        self._cache_progress.setFixedHeight(16)
        self._cache_progress.setTextVisible(True)
        self._cache_progress.setFormat(_tr("loading.cache.computing"))
        self._cache_progress.setStyleSheet(
            "QProgressBar { background:#1a1a32; border:1px solid #2a2a4a; border-radius:4px; color:#aaa; font-size:10px; }"
            "QProgressBar::chunk { background:#3f8f72; border-radius:3px; }"
        )
        self._cache_progress.hide()
        self.statusBar().addPermanentWidget(self._cache_progress)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)

        # Use initial_save if provided, otherwise try default save, otherwise defer to user selection
        save_to_load = initial_save or _saved_default_save()
        if save_to_load:
            # Defer load_save to after the window is shown so the UI appears instantly
            QTimer.singleShot(0, lambda: self.load_save(save_to_load))

    # ── Menu ──────────────────────────────────────────────────────────────

    def _build_menu(self):
        self.menuBar().clear()
        fm = self.menuBar().addMenu(_tr("menu.file"))

        oa = QAction(_tr("menu.file.open_save"), self)
        oa.setShortcut("Ctrl+O")
        oa.triggered.connect(self._open_file)
        fm.addAction(oa)

        # Recent Saves submenu
        self._recent_saves_menu = fm.addMenu(_tr("menu.file.recent_saves"))
        self._recent_save_actions: list[QAction] = []
        self._refresh_recent_save_actions()

        fm.addSeparator()

        # Default Save submenu
        self._default_save_menu = fm.addMenu(_tr("menu.file.default_save"))
        self._set_default_save_action = QAction(_tr("menu.file.default_save.set_current"), self)
        self._set_default_save_action.triggered.connect(self._set_current_as_default)
        self._set_default_save_action.setEnabled(False)
        self._default_save_menu.addAction(self._set_default_save_action)

        self._clear_default_save_action = QAction(_tr("menu.file.default_save.clear"), self)
        self._clear_default_save_action.triggered.connect(self._clear_default_save)
        self._clear_default_save_action.setEnabled(False)
        self._default_save_menu.addAction(self._clear_default_save_action)

        fm.addSeparator()

        ra = QAction(_tr("menu.file.reload"), self)
        ra.setShortcut("F5")
        ra.triggered.connect(self._reload)
        fm.addAction(ra)

        recalc = QAction(_tr("menu.file.recalculate_breeding_data"), self)
        recalc.setShortcut("Ctrl+F5")
        recalc.setToolTip(_tr("menu.file.recalculate_breeding_data.tooltip"))
        recalc.triggered.connect(lambda: self._start_breeding_cache(self._cats, force_full=True) if self._cats else None)
        fm.addAction(recalc)

        clear_cache = QAction(_tr("menu.file.clear_breeding_cache"), self)
        clear_cache.setToolTip(_tr("menu.file.clear_breeding_cache.tooltip"))
        clear_cache.triggered.connect(self._clear_breeding_cache)
        fm.addAction(clear_cache)

        fm.addSeparator()

        exit_action = QAction(_tr("menu.file.exit"), self)
        exit_action.setShortcut("Alt+F4")
        exit_action.triggered.connect(self.close)
        fm.addAction(exit_action)

        sm = self.menuBar().addMenu(_tr("menu.settings"))
        locations_action = QAction(_tr("menu.settings.locations"), self)
        locations_action.triggered.connect(self._open_locations_dialog)
        sm.addAction(locations_action)

        sm.addSeparator()
        self._language_menu = sm.addMenu(_tr("language.menu"))
        self._language_group = QActionGroup(self)
        self._language_group.setExclusive(True)
        for language in _SUPPORTED_LANGUAGES:
            action = QAction(_language_label(language), self)
            action.setCheckable(True)
            action.setChecked(language == _current_language())
            action.triggered.connect(lambda checked=False, lang=language: self._change_language(lang))
            self._language_group.addAction(action)
            self._language_menu.addAction(action)

        sm.addSeparator()
        self._lineage_action = QAction(_tr("menu.settings.show_lineage"), self)
        self._lineage_action.setCheckable(True)
        self._lineage_action.setChecked(self._show_lineage)
        self._lineage_action.triggered.connect(self._toggle_lineage)
        sm.addAction(self._lineage_action)

        sm.addSeparator()
        zoom_in = QAction(_tr("menu.settings.zoom_in"), self)
        zoom_in_keys = QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomIn)
        if not zoom_in_keys:
            zoom_in_keys = []
        for seq in (QKeySequence("Ctrl+="), QKeySequence("Ctrl++")):
            if seq not in zoom_in_keys:
                zoom_in_keys.append(seq)
        zoom_in.setShortcuts(zoom_in_keys)
        zoom_in.triggered.connect(lambda: self._change_zoom(+1))
        sm.addAction(zoom_in)

        zoom_out = QAction(_tr("menu.settings.zoom_out"), self)
        zoom_out_keys = QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomOut)
        if not zoom_out_keys:
            zoom_out_keys = []
        if QKeySequence("Ctrl+-") not in zoom_out_keys:
            zoom_out_keys.append(QKeySequence("Ctrl+-"))
        zoom_out.setShortcuts(zoom_out_keys)
        zoom_out.triggered.connect(lambda: self._change_zoom(-1))
        sm.addAction(zoom_out)

        zoom_reset = QAction(_tr("menu.settings.reset_zoom"), self)
        zoom_reset.setShortcut("Ctrl+0")
        zoom_reset.triggered.connect(self._reset_zoom)
        sm.addAction(zoom_reset)

        self._zoom_info_action = QAction("", self)
        self._zoom_info_action.setEnabled(False)
        sm.addAction(self._zoom_info_action)
        self._update_zoom_info_action()

        sm.addSeparator()
        fs_in = QAction(_tr("menu.settings.increase_font_size"), self)
        fs_in.setShortcut("Ctrl+]")
        fs_in.triggered.connect(lambda: self._change_font_size(+1))
        sm.addAction(fs_in)

        fs_out = QAction(_tr("menu.settings.decrease_font_size"), self)
        fs_out.setShortcut("Ctrl+[")
        fs_out.triggered.connect(lambda: self._change_font_size(-1))
        sm.addAction(fs_out)

        fs_reset = QAction(_tr("menu.settings.reset_font_size"), self)
        fs_reset.setShortcut("Ctrl+\\")
        fs_reset.triggered.connect(lambda: self._set_font_size_offset(0))
        sm.addAction(fs_reset)

        self._font_size_info_action = QAction("", self)
        self._font_size_info_action.setEnabled(False)
        sm.addAction(self._font_size_info_action)
        self._update_font_size_info_action()

    def _refresh_recent_save_actions(self):
        if not hasattr(self, "_recent_saves_menu"):
            return
        self._recent_saves_menu.clear()
        self._recent_save_actions = []

        saves = find_save_files()
        if not saves:
            action = QAction(_tr("menu.file.no_saves_found", path=_save_root_dir()), self)
            action.setEnabled(False)
            self._recent_saves_menu.addAction(action)
            self._recent_save_actions.append(action)
            return

        for path in saves[:10]:
            action = QAction(os.path.basename(path), self)
            action.setToolTip(path)
            action.triggered.connect(lambda _, p=path: self.load_save(p))
            self._recent_saves_menu.addAction(action)
            self._recent_save_actions.append(action)

    def _open_locations_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(_tr("dialog.locations.title"))
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        game_title = QLabel(_tr("dialog.locations.game_install"))
        game_title.setStyleSheet(_NAME_STYLE)
        game_path_label = QLabel()
        game_path_label.setWordWrap(True)
        game_path_label.setStyleSheet(_META_STYLE)

        save_title = QLabel(_tr("dialog.locations.save_root"))
        save_title.setStyleSheet(_NAME_STYLE)
        save_path_label = QLabel()
        save_path_label.setWordWrap(True)
        save_path_label.setStyleSheet(_META_STYLE)

        note_label = QLabel(_tr("dialog.locations.note", path=APPDATA_SAVE_DIR))
        note_label.setWordWrap(True)
        note_label.setStyleSheet(_META_STYLE)

        def _refresh_labels():
            game_path_label.setText(_GPAK_PATH or _tr("common.not_found"))
            save_path_label.setText(_save_root_dir())

        def _choose_game_dir():
            start_dir = os.path.dirname(_GPAK_PATH) if _GPAK_PATH else (
                r"C:\Program Files (x86)\Steam\steamapps\common\Mewgenics"
                if os.path.isdir(r"C:\Program Files (x86)\Steam\steamapps\common\Mewgenics")
                else (
                    r"C:\Program Files\Steam\steamapps\common\Mewgenics"
                    if os.path.isdir(r"C:\Program Files\Steam\steamapps\common\Mewgenics")
                    else str(Path.home())
                )
            )
            chosen_dir = QFileDialog.getExistingDirectory(
                dlg,
                _tr("dialog.locations.select_game_folder"),
                start_dir,
            )
            if not chosen_dir:
                return
            gpak_path = os.path.join(chosen_dir, "resources.gpak")
            if not os.path.exists(gpak_path):
                QMessageBox.warning(
                    dlg,
                    _tr("dialog.locations.resources_not_found.title"),
                    _tr("dialog.locations.resources_not_found.body"),
                )
                return
            _set_gpak_path(gpak_path)
            _refresh_labels()
            if self._current_save:
                self.load_save(self._current_save)
            self.statusBar().showMessage(_tr("status.using_game_data", path=gpak_path))

        def _choose_save_dir():
            chosen_dir = QFileDialog.getExistingDirectory(
                dlg,
                _tr("dialog.locations.select_save_root"),
                _save_root_dir(),
            )
            if not chosen_dir:
                return
            _set_save_dir(chosen_dir)
            _refresh_labels()
            self._refresh_recent_save_actions()
            self.statusBar().showMessage(_tr("status.using_save_root", path=chosen_dir))

        game_btn = QPushButton(_tr("dialog.locations.change_game_folder"))
        game_btn.clicked.connect(_choose_game_dir)
        save_btn = QPushButton(_tr("dialog.locations.change_save_root"))
        save_btn.clicked.connect(_choose_save_dir)

        layout.addWidget(game_title)
        layout.addWidget(game_path_label)
        layout.addWidget(game_btn)
        layout.addSpacing(8)
        layout.addWidget(save_title)
        layout.addWidget(save_path_label)
        layout.addWidget(save_btn)
        layout.addSpacing(8)
        layout.addWidget(note_label)

        close_btn = QPushButton(_tr("common.close"))
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

        _refresh_labels()
        dlg.resize(640, 260)
        dlg.exec()

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
        # Snapshot all stylesheet font sizes before any offset is applied,
        # so _apply_font_offset_to_tree always scales from the true originals.
        _apply_font_offset_to_tree(central, 0)

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

        self._filters_section_label = sl(_tr("sidebar.section.filters"))
        vb.addWidget(self._filters_section_label)
        self._btn_everyone = _sidebar_btn(_tr("sidebar.button.all_cats"))
        self._btn_everyone.clicked.connect(
            lambda: self._filter("__all__", self._btn_everyone))
        vb.addWidget(self._btn_everyone)
        self._room_btns["__all__"] = self._btn_everyone

        self._btn_all = _sidebar_btn(_tr("sidebar.button.alive_cats"))
        self._btn_all.setChecked(True)
        self._active_btn = self._btn_all
        self._btn_all.clicked.connect(lambda: self._filter(None, self._btn_all))
        vb.addWidget(self._btn_all)
        self._room_btns[None] = self._btn_all

        self._btn_exceptional = _sidebar_btn(f"{_tr('sidebar.button.exceptional')}  (>= {EXCEPTIONAL_SUM_THRESHOLD})")
        self._btn_exceptional.setToolTip(
            _tr(
                "sidebar.tooltip.exceptional",
                default="Exceptional breeders: base stat sum >= {threshold}.",
                threshold=EXCEPTIONAL_SUM_THRESHOLD,
            )
        )
        self._btn_exceptional.clicked.connect(
            lambda: self._filter("__exceptional__", self._btn_exceptional)
        )
        vb.addWidget(self._btn_exceptional)
        self._room_btns["__exceptional__"] = self._btn_exceptional

        self._btn_donation = _sidebar_btn(f"{_tr('sidebar.button.donation_candidates')}  (<= {DONATION_SUM_THRESHOLD})")
        self._btn_donation.setToolTip(
            _tr(
                "sidebar.tooltip.donation",
                default="Donation candidates use documented heuristics: base stat sum <= {sum_threshold}, top stat <= {top_stat}, and/or high aggression.",
                sum_threshold=DONATION_SUM_THRESHOLD,
                top_stat=DONATION_MAX_TOP_STAT,
            )
        )
        self._btn_donation.clicked.connect(
            lambda: self._filter("__donation__", self._btn_donation)
        )
        vb.addWidget(self._btn_donation)
        self._room_btns["__donation__"] = self._btn_donation

        vb.addWidget(_hsep())
        self._breeding_section_label = sl(_tr("sidebar.section.breeding"))
        vb.addWidget(self._breeding_section_label)
        self._btn_room_optimizer = _sidebar_btn(_tr("sidebar.button.room_optimizer"))
        self._btn_room_optimizer.clicked.connect(self._open_room_optimizer)
        vb.addWidget(self._btn_room_optimizer)
        self._btn_perfect_planner = _sidebar_btn(_tr("sidebar.button.perfect_7_planner"))
        self._btn_perfect_planner.clicked.connect(self._open_perfect_planner_view)
        vb.addWidget(self._btn_perfect_planner)
        self._btn_mutation_planner = _sidebar_btn(_tr("sidebar.button.mutation_planner"))
        self._btn_mutation_planner.clicked.connect(self._open_mutation_planner_view)
        vb.addWidget(self._btn_mutation_planner)
        self._btn_safe_breeding_view = _sidebar_btn(_tr("sidebar.button.safe_breeding"))
        self._btn_safe_breeding_view.clicked.connect(self._open_safe_breeding_view)
        vb.addWidget(self._btn_safe_breeding_view)
        self._btn_breeding_partners_view = _sidebar_btn(_tr("sidebar.button.breeding_partners"))
        self._btn_breeding_partners_view.clicked.connect(self._open_breeding_partners_view)
        vb.addWidget(self._btn_breeding_partners_view)

        vb.addWidget(_hsep())
        self._info_section_label = sl(_tr("sidebar.section.info"))
        vb.addWidget(self._info_section_label)
        self._btn_tree_view = _sidebar_btn(_tr("sidebar.button.family_tree_view"))
        self._btn_tree_view.clicked.connect(self._open_tree_browser)
        vb.addWidget(self._btn_tree_view)
        self._btn_calibration = _sidebar_btn(_tr("sidebar.button.calibration"))
        self._btn_calibration.clicked.connect(self._open_calibration_view)
        vb.addWidget(self._btn_calibration)

        vb.addWidget(_hsep())
        self._rooms_section_label = sl(_tr("sidebar.section.rooms"))
        vb.addWidget(self._rooms_section_label)
        self._rooms_vb = QVBoxLayout(); self._rooms_vb.setSpacing(2)
        vb.addLayout(self._rooms_vb)
        vb.addWidget(_hsep())

        self._other_section_label = sl(_tr("sidebar.section.other"))
        vb.addWidget(self._other_section_label)
        self._btn_adventure = _sidebar_btn(_tr("sidebar.button.on_adventure"))
        self._btn_gone      = _sidebar_btn(_tr("sidebar.button.gone"))
        self._btn_adventure.clicked.connect(
            lambda: self._filter("__adventure__", self._btn_adventure))
        self._btn_gone.clicked.connect(
            lambda: self._filter("__gone__", self._btn_gone))
        vb.addWidget(self._btn_adventure)
        vb.addWidget(self._btn_gone)
        self._room_btns["__adventure__"] = self._btn_adventure
        self._room_btns["__gone__"]      = self._btn_gone

        vb.addStretch()

        self._save_lbl = QLabel(_tr("sidebar.no_save_loaded"))
        self._save_lbl.setStyleSheet("color:#444; font-size:10px;")
        self._save_lbl.setWordWrap(True)
        vb.addWidget(self._save_lbl)

        self._reload_btn = QPushButton(_tr("sidebar.button.reload"))
        self._reload_btn.setStyleSheet("QPushButton { color:#888; background:#1a1a32;"
                         " border:1px solid #2a2a4a; padding:7px;"
                         " border-radius:4px; font-size:11px; }"
                         "QPushButton:hover { background:#222244; }")
        self._reload_btn.clicked.connect(self._reload)
        vb.addWidget(self._reload_btn)
        return w

    def _rebuild_room_buttons(self, cats: list[Cat]):
        while self._rooms_vb.count():
            item = self._rooms_vb.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        _ROOM_ORDER = {
            "Attic": 0,
            "Floor2_Large": 1, "Floor2_Small": 2,
            "Floor1_Large": 3, "Floor1_Small": 4,
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

    def _refresh_filter_button_counts(self):
        total = len(self._cats)
        alive = sum(1 for c in self._cats if c.status != "Gone")
        exceptional = sum(1 for c in self._cats if c.status != "Gone" and _is_exceptional_breeder(c))
        donation = sum(1 for c in self._cats if c.status != "Gone" and _is_donation_candidate(c))
        adv = sum(1 for c in self._cats if c.status == "Adventure")
        gone = sum(1 for c in self._cats if c.status == "Gone")

        self._btn_everyone.setText(f"{_tr('sidebar.button.all_cats')}  ({total})" if total else _tr("sidebar.button.all_cats"))
        self._btn_all.setText(f"{_tr('sidebar.button.alive_cats')}  ({alive})" if total else _tr("sidebar.button.alive_cats"))
        self._btn_exceptional.setText(f"{_tr('sidebar.button.exceptional')}  ({exceptional})")
        self._btn_donation.setText(f"{_tr('sidebar.button.donation_candidates')}  ({donation})")
        self._btn_adventure.setText(f"{_tr('sidebar.button.on_adventure')}  ({adv})" if total else _tr("sidebar.button.on_adventure"))
        self._btn_gone.setText(f"{_tr('sidebar.button.gone')}  ({gone})" if total else _tr("sidebar.button.gone"))
        self._btn_room_optimizer.setText(_tr("sidebar.button.room_optimizer"))
        self._btn_perfect_planner.setText(_tr("sidebar.button.perfect_7_planner"))
        self._btn_mutation_planner.setText(_tr("sidebar.button.mutation_planner"))
        self._btn_safe_breeding_view.setText(_tr("sidebar.button.safe_breeding"))
        self._btn_breeding_partners_view.setText(_tr("sidebar.button.breeding_partners"))
        self._btn_tree_view.setText(_tr("sidebar.button.family_tree_view"))
        self._btn_calibration.setText(_tr("sidebar.button.calibration"))

    def _retranslate_ui(self):
        current_room_key = next((key for key, btn in self._room_btns.items() if btn is self._active_btn), None)
        _refresh_localized_constants()
        self._build_menu()
        self._filters_section_label.setText(_tr("sidebar.section.filters"))
        self._breeding_section_label.setText(_tr("sidebar.section.breeding"))
        self._info_section_label.setText(_tr("sidebar.section.info"))
        self._rooms_section_label.setText(_tr("sidebar.section.rooms"))
        self._other_section_label.setText(_tr("sidebar.section.other"))
        self._reload_btn.setText(_tr("sidebar.button.reload"))
        self._save_lbl.setText(os.path.basename(self._current_save) if self._current_save else _tr("sidebar.no_save_loaded"))
        self._search.setPlaceholderText(_tr("header.search_placeholder"))
        self._loading_label.setText(_tr("loading.save_file"))
        self._cache_progress.setFormat(_tr("loading.cache.computing"))
        self._refresh_filter_button_counts()
        self._rebuild_room_buttons(self._cats)
        if current_room_key in self._room_btns:
            self._active_btn = self._room_btns[current_room_key]
            self._active_btn.setChecked(True)
        self._update_header(current_room_key)
        self._update_count()
        self._refresh_bulk_view_buttons()
        if hasattr(self, "_source_model") and self._source_model is not None:
            self._source_model.headerDataChanged.emit(Qt.Horizontal, 0, len(COLUMNS) - 1)
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.retranslate_ui()
        if self._breeding_partners_view is not None:
            self._breeding_partners_view.retranslate_ui()
        if self._calibration_view is not None:
            self._calibration_view.retranslate_ui()
        if self._tree_view is not None:
            self._tree_view.retranslate_ui()
        if self._breeding_partners_view is not None:
            self._breeding_partners_view.retranslate_ui()
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.retranslate_ui()
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.retranslate_ui()
        if self._mutation_planner_view is not None:
            self._mutation_planner_view.retranslate_ui()
        self._on_selection()

    def _change_language(self, language: str):
        if language not in _SUPPORTED_LANGUAGES or language == _current_language():
            return
        _set_saved_language(language)
        _set_current_language(language)
        self._retranslate_ui()
        current_title = _language_label(language)
        self.setWindowTitle(_tr("app.title_with_save", name=os.path.basename(self._current_save)) if self._current_save else _tr("app.title"))
        self.statusBar().showMessage(_tr("status.language_changed", language=current_title))

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
        self._header_lbl = QLabel(_tr("header.filter.all_cats"))
        self._header_lbl.setStyleSheet("color:#eee; font-size:15px; font-weight:bold;")
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color:#555; font-size:12px; padding-left:8px;")
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet("color:#4a7a9a; font-size:11px;")
        self._bulk_blacklist_btn = QPushButton()
        self._bulk_blacklist_btn.setCheckable(True)
        self._bulk_blacklist_btn.setMinimumWidth(130)
        self._bulk_blacklist_btn.setStyleSheet(
            "QPushButton { background:#5a2d22; color:#f1dfda; border:1px solid #8b4c3e;"
            " border-radius:4px; padding:4px 10px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#6c382a; }"
            "QPushButton:pressed { background:#4c241b; }"
            "QPushButton:checked { background:#7a3626; border:1px solid #b35b48; }"
        )
        self._set_bulk_toggle_label(self._bulk_blacklist_btn, _tr("bulk.breeding_block"), False)
        self._bulk_blacklist_btn.clicked.connect(self._toggle_blacklist_filtered_cats)
        self._bulk_must_breed_btn = QPushButton()
        self._bulk_must_breed_btn.setCheckable(True)
        self._bulk_must_breed_btn.setMinimumWidth(110)
        self._bulk_must_breed_btn.setStyleSheet(
            "QPushButton { background:#3b355f; color:#ece8fb; border:1px solid #5d58a0;"
            " border-radius:4px; padding:4px 10px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#49417a; }"
            "QPushButton:pressed { background:#312c4f; }"
            "QPushButton:checked { background:#514890; border:1px solid #7d73c7; }"
        )
        self._set_bulk_toggle_label(self._bulk_must_breed_btn, _tr("bulk.must_breed"), False)
        self._bulk_must_breed_btn.clicked.connect(self._toggle_must_breed_filtered_cats)
        bulk_container = QWidget()
        self._bulk_actions_layout = QHBoxLayout(bulk_container)
        self._bulk_actions_layout.setContentsMargins(0, 0, 0, 0)
        self._bulk_actions_layout.setSpacing(8)
        self._bulk_actions_layout.addWidget(self._bulk_must_breed_btn)
        self._bulk_actions_layout.addWidget(self._bulk_blacklist_btn)
        self._search = QLineEdit()
        self._search.setPlaceholderText(_tr("header.search_placeholder"))
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(self._base_search_width)
        self._search.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:3px 8px; font-size:12px; }"
            "QLineEdit:focus { border-color:#3a3a7a; }")
        hb.addWidget(self._header_lbl)
        hb.addWidget(self._count_lbl)
        hb.addStretch()
        hb.addWidget(bulk_container)
        hb.addSpacing(10)
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
        self._source_model.blacklistChanged.connect(self._on_blacklist_changed)
        self._proxy_model  = RoomFilterModel()
        self._proxy_model.setSourceModel(self._source_model)
        self._proxy_model.modelReset.connect(self._update_count)
        self._proxy_model.rowsInserted.connect(self._update_count)
        self._proxy_model.rowsRemoved.connect(self._update_count)

        self._table = QTableView()
        self._table.setModel(self._proxy_model)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(COL_NAME, Qt.AscendingOrder)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        # Checkbox columns are toggled explicitly in _on_table_clicked.
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)  # we control stretch manually

        # Name: interactive so the user can resize it; not Stretch so it
        # doesn't eat the blank space that should sit at the right edge.
        hh.setSectionResizeMode(COL_NAME, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_NAME, self._base_col_widths[COL_NAME])

        # Room: size to content so it adapts to room name length
        hh.setSectionResizeMode(COL_ROOM, QHeaderView.ResizeToContents)

        # Narrow fixed columns (gender, status, stats, sum)
        for col, width in [
            (COL_GEN, _W_GEN),
            (COL_STAT, _W_STATUS),
            (COL_BL, 34),
            (COL_MB, 34),
            (COL_SUM, 38),
            (COL_AGG, _W_TRAIT),
            (COL_LIB, _W_TRAIT),
            (COL_INBRD, _W_TRAIT),
            (COL_SEXUALITY, _W_TRAIT),
        ] + [(c, _W_STAT) for c in STAT_COLS]:
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
            self._table.setColumnWidth(col, width)

        # Abilities: interactive — user drags to taste
        hh.setSectionResizeMode(COL_ABIL, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_ABIL, self._base_col_widths[COL_ABIL])

        # Mutations: interactive
        hh.setSectionResizeMode(COL_MUTS, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_MUTS, self._base_col_widths[COL_MUTS])

        # Relations: interactive
        hh.setSectionResizeMode(COL_RELNS, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_RELNS, self._base_col_widths[COL_RELNS])

        # Generation depth: fixed narrow, hidden by default (behind lineage toggle)
        hh.setSectionResizeMode(COL_REL, QHeaderView.Fixed)
        self._table.setColumnWidth(COL_REL, self._base_col_widths[COL_REL])

        # Generation depth: fixed narrow, hidden by default (behind lineage toggle)
        hh.setSectionResizeMode(COL_AGE, QHeaderView.Fixed)
        self._table.setColumnWidth(COL_AGE, self._base_col_widths[COL_AGE])

        # Generation depth: fixed narrow, hidden by default (behind lineage toggle)
        hh.setSectionResizeMode(COL_GEN_DEPTH, QHeaderView.Fixed)
        self._table.setColumnWidth(COL_GEN_DEPTH, _W_GEN)
        self._table.setColumnHidden(COL_GEN_DEPTH, True)

        # Source: Stretch — absorbs blank space, hidden by default (behind lineage toggle)
        hh.setSectionResizeMode(COL_SRC, QHeaderView.Stretch)
        self._table.setColumnHidden(COL_SRC, True)

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
        self._table.clicked.connect(self._on_table_clicked)
        self._search.textChanged.connect(self._proxy_model.set_name_filter)
        self._search.textChanged.connect(self._update_count)
        self._search.textChanged.connect(lambda _: self._refresh_bulk_view_buttons())
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
        self._breeding_partners_view = BreedingPartnersView(self)
        self._breeding_partners_view.hide()
        vb.addWidget(self._breeding_partners_view, 1)
        self._room_optimizer_view = RoomOptimizerView(self)
        self._room_optimizer_view.hide()
        vb.addWidget(self._room_optimizer_view, 1)
        self._perfect_planner_view = PerfectCatPlannerView(self)
        self._perfect_planner_view.hide()
        vb.addWidget(self._perfect_planner_view, 1)
        self._calibration_view = CalibrationView(self)
        self._calibration_view.calibrationChanged.connect(self._on_calibration_changed)
        self._calibration_view.hide()
        vb.addWidget(self._calibration_view, 1)
        self._mutation_planner_view = MutationDisorderPlannerView(self)
        self._mutation_planner_view.hide()
        vb.addWidget(self._mutation_planner_view, 1)
        # Wire planner to optimizer so traits can be imported
        self._room_optimizer_view.set_planner_view(self._mutation_planner_view)
        # Allow cat locator tables to navigate to cat in Alive Cats view
        self._mutation_planner_view._navigate_to_cat_callback = self._navigate_to_cat
        self._room_optimizer_view._cat_locator._navigate_to_cat_callback = self._navigate_to_cat
        self._perfect_planner_view._cat_locator._navigate_to_cat_callback = self._navigate_to_cat

        # Loading overlay — shown during background save parse, dismissed before UI population
        self._loading_overlay = QWidget(w)
        self._loading_overlay.setStyleSheet("background:#0a0a18;")
        lo_vb = QVBoxLayout(self._loading_overlay)
        lo_vb.setAlignment(Qt.AlignCenter)
        self._loading_label = QLabel(_tr("loading.save_file"))
        self._loading_label.setStyleSheet("color:#aaa; font-size:15px; font-weight:bold;")
        self._loading_label.setAlignment(Qt.AlignCenter)
        self._loading_bar = QProgressBar()
        self._loading_bar.setFixedWidth(320)
        self._loading_bar.setFixedHeight(16)
        self._loading_bar.setRange(0, 0)  # indeterminate pulse
        self._loading_bar.setTextVisible(False)
        self._loading_bar.setStyleSheet(
            "QProgressBar { background:#1a1a32; border:1px solid #2a2a4a; border-radius:4px; }"
            "QProgressBar::chunk { background:#3f8f72; border-radius:3px; }"
        )
        lo_vb.addWidget(self._loading_label)
        lo_vb.addSpacing(10)
        lo_vb.addWidget(self._loading_bar, 0, Qt.AlignCenter)
        self._loading_overlay.hide()

        return w

    # ── Selection → detail ────────────────────────────────────────────────

    def _on_selection(self):
        rows = list({
            self._proxy_model.mapToSource(idx).row()
            for idx in self._table.selectionModel().selectedRows()
        })
        cats = [c for r in rows[:2] if (c := self._source_model.cat_at(r)) is not None]
        if len(cats) == 2 and _is_hater_pair(cats[0], cats[1]):
            cats = cats[:1]
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

    def _on_table_clicked(self, proxy_index: QModelIndex):
        if not proxy_index.isValid() or proxy_index.column() not in (COL_BL, COL_MB):
            return
        src_index = self._proxy_model.mapToSource(proxy_index)
        if not src_index.isValid():
            return
        current = self._source_model.data(src_index, Qt.CheckStateRole)
        next_state = Qt.Unchecked if current == Qt.Checked else Qt.Checked
        if self._source_model.setData(src_index, next_state, Qt.CheckStateRole):
            self._on_selection()

    # ── Filtering ──────────────────────────────────────────────────────────

    def _filter(self, room_key, btn: QPushButton):
        if not getattr(self, "_save_view_disabled", False):
            _save_current_view("table")
        self._show_table_view()
        if self._active_btn and self._active_btn is not btn:
            self._active_btn.setChecked(False)
        btn.setChecked(True)
        self._active_btn = btn
        self._proxy_model.set_room(room_key)
        self._refresh_bulk_view_buttons(room_key)
        self._update_header(room_key)
        self._update_count()
        self._detail.show_cats([])
        self._source_model.set_focus_cat(None)

    def _visible_filtered_cats(self) -> list[Cat]:
        cats: list[Cat] = []
        for row in range(self._proxy_model.rowCount()):
            src_idx = self._proxy_model.mapToSource(self._proxy_model.index(row, 0))
            if not src_idx.isValid():
                continue
            cat = self._source_model.cat_at(src_idx.row())
            if cat is not None:
                cats.append(cat)
        return cats

    def _selected_cats(self) -> list[Cat]:
        cats: list[Cat] = []
        for idx in self._table.selectionModel().selectedRows():
            src_idx = self._proxy_model.mapToSource(idx)
            if not src_idx.isValid():
                continue
            cat = self._source_model.cat_at(src_idx.row())
            if cat is not None:
                cats.append(cat)
        return cats

    def _refresh_bulk_view_buttons(self, room_key=None):
        if room_key is None and self._active_btn is not None:
            for key, btn in self._room_btns.items():
                if btn is self._active_btn:
                    room_key = key
                    break
        visible = room_key in (None, "__donation__", "__exceptional__")
        donation_view = room_key == "__donation__"
        exceptional_view = room_key == "__exceptional__"
        alive_view = room_key is None
        if hasattr(self, "_bulk_actions_layout"):
            while self._bulk_actions_layout.count():
                item = self._bulk_actions_layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
            if donation_view:
                self._bulk_actions_layout.addWidget(self._bulk_blacklist_btn)
                self._bulk_actions_layout.addWidget(self._bulk_must_breed_btn)
            else:
                self._bulk_actions_layout.addWidget(self._bulk_must_breed_btn)
                self._bulk_actions_layout.addWidget(self._bulk_blacklist_btn)
        if hasattr(self, "_bulk_blacklist_btn"):
            self._bulk_blacklist_btn.setVisible(visible)
        if hasattr(self, "_bulk_must_breed_btn"):
            self._bulk_must_breed_btn.setVisible(visible)
        if not visible:
            return
        if alive_view:
            self._bulk_blacklist_btn.blockSignals(True)
            self._bulk_blacklist_btn.setCheckable(False)
            self._bulk_blacklist_btn.setText(_tr("bulk.toggle_breeding_block"))
            self._bulk_blacklist_btn.setEnabled(True)
            self._bulk_blacklist_btn.setToolTip(_tr("bulk.toggle_breeding_block.tooltip"))
            self._bulk_blacklist_btn.blockSignals(False)
            self._bulk_must_breed_btn.blockSignals(True)
            self._bulk_must_breed_btn.setCheckable(False)
            self._bulk_must_breed_btn.setText(_tr("bulk.toggle_must_breed"))
            self._bulk_must_breed_btn.setEnabled(True)
            self._bulk_must_breed_btn.setToolTip(_tr("bulk.toggle_must_breed.tooltip"))
            self._bulk_must_breed_btn.blockSignals(False)
            return
        cats = self._visible_filtered_cats()
        all_blocked = bool(cats) and all(cat.is_blacklisted for cat in cats)
        all_must_breed = bool(cats) and all(cat.must_breed for cat in cats)
        self._bulk_blacklist_btn.setCheckable(True)
        self._bulk_blacklist_btn.blockSignals(True)
        if exceptional_view:
            any_blocked = any(cat.is_blacklisted for cat in cats)
            self._bulk_blacklist_btn.setChecked(False)
            self._bulk_blacklist_btn.setEnabled(any_blocked)
            self._bulk_blacklist_btn.setText(_tr("bulk.clear_breeding_block"))
            self._bulk_blacklist_btn.setToolTip(_tr("bulk.clear_breeding_block.tooltip"))
        else:
            self._bulk_blacklist_btn.setChecked(all_blocked)
            self._bulk_blacklist_btn.setEnabled(True)
            self._set_bulk_toggle_label(self._bulk_blacklist_btn, _tr("bulk.breeding_block"), all_blocked)
            self._bulk_blacklist_btn.setToolTip("")
        self._bulk_blacklist_btn.blockSignals(False)
        self._bulk_must_breed_btn.setCheckable(True)
        self._bulk_must_breed_btn.blockSignals(True)
        if donation_view:
            any_must_breed = any(cat.must_breed for cat in cats)
            self._bulk_must_breed_btn.setChecked(False)
            self._bulk_must_breed_btn.setEnabled(any_must_breed)
            self._bulk_must_breed_btn.setText(_tr("bulk.clear_must_breed"))
            self._bulk_must_breed_btn.setToolTip(_tr("bulk.clear_must_breed.tooltip"))
        else:
            self._bulk_must_breed_btn.setChecked(all_must_breed)
            self._bulk_must_breed_btn.setEnabled(True)
            self._set_bulk_toggle_label(self._bulk_must_breed_btn, _tr("bulk.must_breed"), all_must_breed)
            self._bulk_must_breed_btn.setToolTip("")
        self._bulk_must_breed_btn.blockSignals(False)

    def _toggle_blacklist_filtered_cats(self):
        room_key = None
        if self._active_btn is not None:
            for key, btn in self._room_btns.items():
                if btn is self._active_btn:
                    room_key = key
                    break
        alive_view = room_key is None
        exceptional_view = room_key == "__exceptional__"
        if alive_view:
            cats = self._selected_cats()
            if not cats:
                self.statusBar().showMessage(_tr("bulk.status.select_toggle_breeding_block", default="Select cats first, then click Toggle Breeding Block"))
                return
            changed = 0
            for cat in cats:
                cat.is_blacklisted = not cat.is_blacklisted
                if cat.is_blacklisted:
                    cat.must_breed = False
                changed += 1
            self._emit_bulk_toggle_refresh()
            self.statusBar().showMessage(_tr("bulk.status.toggled_breeding_block", default="Toggled breeding block for {count} selected cats", count=changed))
            return
        target_state = False if exceptional_view else self._bulk_blacklist_btn.isChecked()
        changed = 0
        for cat in self._visible_filtered_cats():
            if cat.is_blacklisted == target_state and (not target_state or not cat.must_breed):
                continue
            cat.is_blacklisted = target_state
            if target_state:
                cat.must_breed = False
            changed += 1
        self._refresh_bulk_view_buttons()
        if changed == 0:
            self.statusBar().showMessage(_tr("bulk.status.no_breeding_block_change", default="No cats in view needed a breeding-block change"))
            return
        self._emit_bulk_toggle_refresh()
        if exceptional_view:
            self.statusBar().showMessage(_tr("bulk.status.cleared_breeding_block_exceptional", default="Cleared breeding block for {count} cats in the current exceptional view", count=changed))
        else:
            state_text = _tr("common.on", default="on") if target_state else _tr("common.off", default="off")
            self.statusBar().showMessage(_tr("bulk.status.turned_breeding_block", default="Turned breeding block {state} for {count} cats in the current view", state=state_text, count=changed))

    def _toggle_must_breed_filtered_cats(self):
        room_key = None
        if self._active_btn is not None:
            for key, btn in self._room_btns.items():
                if btn is self._active_btn:
                    room_key = key
                    break
        alive_view = room_key is None
        donation_view = room_key == "__donation__"
        if alive_view:
            cats = self._selected_cats()
            if not cats:
                self.statusBar().showMessage(_tr("bulk.status.select_toggle_must_breed", default="Select cats first, then click Toggle Must Breed"))
                return
            changed = 0
            for cat in cats:
                cat.must_breed = not cat.must_breed
                if cat.must_breed:
                    cat.is_blacklisted = False
                changed += 1
            self._emit_bulk_toggle_refresh()
            self.statusBar().showMessage(_tr("bulk.status.toggled_must_breed", default="Toggled must breed for {count} selected cats", count=changed))
            return
        target_state = False if donation_view else self._bulk_must_breed_btn.isChecked()
        changed = 0
        for cat in self._visible_filtered_cats():
            if cat.must_breed == target_state and (not target_state or not cat.is_blacklisted):
                continue
            cat.must_breed = target_state
            if target_state:
                cat.is_blacklisted = False
            changed += 1
        self._refresh_bulk_view_buttons()
        if changed == 0:
            self.statusBar().showMessage(_tr("bulk.status.no_must_breed_change", default="No cats in view needed a must-breed change"))
            return
        self._emit_bulk_toggle_refresh()
        if donation_view:
            self.statusBar().showMessage(_tr("bulk.status.cleared_must_breed_donation", default="Cleared Must Breed for {count} cats in the current donation-candidates view", count=changed))
        else:
            state_text = _tr("common.on", default="on") if target_state else _tr("common.off", default="off")
            self.statusBar().showMessage(_tr("bulk.status.turned_must_breed", default="Turned must breed {state} for {count} cats in the current view", state=state_text, count=changed))

    def _emit_bulk_toggle_refresh(self):
        if self._source_model.rowCount() == 0:
            return
        top_left = self._source_model.index(0, COL_BL)
        bottom_right = self._source_model.index(max(0, self._source_model.rowCount() - 1), COL_MB)
        self._source_model.dataChanged.emit(
            top_left,
            bottom_right,
            [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole],
        )
        self._proxy_model.invalidate()
        self._source_model.blacklistChanged.emit()
        self._update_count()
        self._refresh_bulk_view_buttons()

    def _blacklist_filtered_cats(self):
        changed = 0
        for row in range(self._proxy_model.rowCount()):
            proxy_idx = self._proxy_model.index(row, COL_BL)
            if not proxy_idx.isValid():
                continue
            src_idx = self._proxy_model.mapToSource(proxy_idx)
            if not src_idx.isValid():
                continue
            cat = self._source_model.cat_at(src_idx.row())
            if cat is None or cat.is_blacklisted:
                continue
            cat.is_blacklisted = True
            changed += 1
        if changed == 0:
            self.statusBar().showMessage(_tr("bulk.status.no_additional_blacklist", default="No additional cats in view were added to the breeding blacklist"))
            return

        top_left = self._source_model.index(0, COL_BL)
        bottom_right = self._source_model.index(max(0, self._source_model.rowCount() - 1), COL_BL)
        self._source_model.dataChanged.emit(
            top_left,
            bottom_right,
            [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole],
        )
        self._source_model.blacklistChanged.emit()
        self._update_count()
        self.statusBar().showMessage(_tr("bulk.status.excluded_donation", default="Excluded {count} cats in the current donation-candidates view from breeding", count=changed))

    def _clear_must_breed_filtered_cats(self):
        changed = 0
        for row in range(self._proxy_model.rowCount()):
            proxy_idx = self._proxy_model.index(row, COL_MB)
            if not proxy_idx.isValid():
                continue
            src_idx = self._proxy_model.mapToSource(proxy_idx)
            if not src_idx.isValid():
                continue
            cat = self._source_model.cat_at(src_idx.row())
            if cat is None or not cat.must_breed:
                continue
            cat.must_breed = False
            changed += 1
        if changed == 0:
            self.statusBar().showMessage(_tr("bulk.status.no_must_breed_set", default="No cats in view had Must Breed set"))
            return

        top_left = self._source_model.index(0, COL_MB)
        bottom_right = self._source_model.index(max(0, self._source_model.rowCount() - 1), COL_MB)
        self._source_model.dataChanged.emit(
            top_left,
            bottom_right,
            [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole],
        )
        self._source_model.blacklistChanged.emit()
        self._update_count()
        self.statusBar().showMessage(_tr("bulk.status.cleared_must_breed_donation", default="Cleared Must Breed for {count} cats in the current donation-candidates view", count=changed))

    def _show_table_view(self):
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if hasattr(self, "_header"):
            self._header.show()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)

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
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if self._tree_view is not None:
            self._tree_view.set_cats(self._cats)
            self._tree_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(True)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)

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
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.set_cats(self._cats)
            self._safe_breeding_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(True)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)

    def _show_breeding_partners_view(self):
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
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if self._breeding_partners_view is not None:
            self._breeding_partners_view.set_cats(self._cats)
            self._breeding_partners_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(True)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)

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
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.set_cats(self._cats)
            self._room_optimizer_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(True)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)

    def _show_perfect_planner_view(self):
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
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.set_cats(self._cats)
            self._perfect_planner_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(True)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)

    def _show_calibration_view(self):
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
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if self._calibration_view is not None:
            if self._current_save:
                self._calibration_view.set_context(self._current_save, self._cats)
            self._calibration_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(True)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()

    def _show_mutation_planner_view(self):
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
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if self._mutation_planner_view is not None:
            self._mutation_planner_view.set_cats(self._cats)
            self._mutation_planner_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(True)

    def _navigate_to_cat(self, db_key: int):
        """Switch to Alive Cats view and select the given cat by db_key."""
        self._filter(None, self._btn_all)
        for row in range(self._proxy_model.rowCount()):
            src_idx = self._proxy_model.mapToSource(self._proxy_model.index(row, 0))
            cat = self._source_model.cat_at(src_idx.row())
            if cat is not None and cat.db_key == db_key:
                self._table.scrollTo(self._proxy_model.index(row, 0))
                self._table.selectRow(row)
                return
        # Not found in Alive filter — try All Cats
        self._filter("__all__", self._btn_everyone)
        for row in range(self._proxy_model.rowCount()):
            src_idx = self._proxy_model.mapToSource(self._proxy_model.index(row, 0))
            cat = self._source_model.cat_at(src_idx.row())
            if cat is not None and cat.db_key == db_key:
                self._table.scrollTo(self._proxy_model.index(row, 0))
                self._table.selectRow(row)
                return

    def _update_header(self, room_key):
        if room_key == "__all__":
            self._header_lbl.setText(_tr("header.filter.all_cats"))
        elif room_key is None:
            self._header_lbl.setText(_tr("header.filter.alive"))
        elif room_key == "__exceptional__":
            self._header_lbl.setText(_tr("header.filter.exceptional"))
        elif room_key == "__donation__":
            self._header_lbl.setText(_tr("header.filter.donation"))
        elif room_key == "__gone__":
            self._header_lbl.setText(_tr("header.filter.gone"))
        elif room_key == "__adventure__":
            self._header_lbl.setText(_tr("header.filter.adventure"))
        else:
            self._header_lbl.setText(ROOM_DISPLAY.get(room_key, room_key))

    def _update_count(self):
        visible = self._proxy_model.rowCount()
        total   = self._source_model.rowCount()
        self._count_lbl.setText(_tr("header.count", visible=visible, total=total))

        placed = sum(1 for c in self._cats if c.status == "In House")
        adv    = sum(1 for c in self._cats if c.status == "Adventure")
        gone   = sum(1 for c in self._cats if c.status == "Gone")
        self._summary_lbl.setText(_tr("header.summary", placed=placed, adv=adv, gone=gone))

    def _on_blacklist_changed(self):
        if self._current_save:
            _save_blacklist(self._current_save, self._cats)
            _save_must_breed(self._current_save, self._cats)
        self._refresh_bulk_view_buttons()
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.set_cats(self._cats)
        if self._breeding_partners_view is not None:
            self._breeding_partners_view.set_cats(self._cats)
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.set_cats(self._cats)
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.set_cats(self._cats)

    def _on_calibration_changed(self):
        if not self._current_save:
            return
        cal_explicit, cal_token, cal_rows = _apply_calibration(self._current_save, self._cats)
        self._source_model.load(self._cats)
        self._refresh_filter_button_counts()
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.set_cats(self._cats)
        if self._breeding_partners_view is not None:
            self._breeding_partners_view.set_cats(self._cats)
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.set_cats(self._cats)
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.set_cats(self._cats)
        if self._calibration_view is not None and self._calibration_view.isVisible():
            self._calibration_view.set_context(self._current_save, self._cats)
        self._update_count()
        self.statusBar().showMessage(
            _tr(
                "status.calibration_applied",
                default="Calibration applied ({explicit} explicit, {token} token from {rows} rows)",
                explicit=cal_explicit,
                token=cal_token,
                rows=cal_rows,
            )
        )

    # ── Breeding cache ──────────────────────────────────────────────────

    def _start_breeding_cache(self, cats: list[Cat], force_full: bool = False):
        """Kick off background computation of the breeding cache."""
        # Cancel any in-progress worker
        if self._cache_worker is not None:
            self._cache_worker.quit()
            self._cache_worker.wait(500)
            self._cache_worker = None

        # Snapshot parent keys before clearing old cache (for incremental update)
        prev_cache = self._breeding_cache if not force_full else None
        prev_parent_keys = dict(self._prev_parent_keys) if hasattr(self, "_prev_parent_keys") and not force_full else {}

        # Record current parent keys for next reload
        self._prev_parent_keys = {
            c.db_key: (
                c.parent_a.db_key if c.parent_a is not None else None,
                c.parent_b.db_key if c.parent_b is not None else None,
            )
            for c in cats
        }

        self._breeding_cache = None
        self._cache_progress.setValue(0)
        self._cache_progress.show()

        # Try loading pairwise data from disk (skip if force_full)
        existing = None
        save_path = self._current_save or ""
        if not force_full and save_path:
            existing = BreedingCache.load_from_disk(save_path)
            if existing is not None:
                self._cache_progress.setFormat(_tr("loading.cache.loading_cached"))
            elif prev_cache is not None:
                self._cache_progress.setFormat(_tr("loading.cache.updating"))
            else:
                self._cache_progress.setFormat(_tr("loading.cache.computing"))
        else:
            self._cache_progress.setFormat(_tr("loading.cache.computing"))

        worker = BreedingCacheWorker(
            cats, save_path=save_path, existing_pairwise=existing,
            prev_cache=prev_cache, prev_parent_keys=prev_parent_keys,
            parent=self,
        )
        worker.progress.connect(self._on_cache_progress)
        worker.phase1_ready.connect(self._on_phase1_ready)
        worker.finished_cache.connect(self._on_cache_ready)
        worker.finished.connect(lambda: self._cache_progress.hide())
        self._cache_worker = worker
        worker.start()

    def _on_cache_progress(self, current: int, total: int):
        self._cache_progress.setMaximum(total)
        self._cache_progress.setValue(current)

    def _clear_breeding_cache(self):
        """Delete the on-disk breeding cache for the current save file."""
        if not self._current_save:
            self.statusBar().showMessage(_tr("status.no_save_loaded_clear"))
            return
        cp = _breeding_cache_path(self._current_save)
        if os.path.exists(cp):
            try:
                os.remove(cp)
                self.statusBar().showMessage(_tr("status.cache_cleared"))
            except OSError as e:
                self.statusBar().showMessage(_tr(
                    "status.cache_delete_failed",
                    default="Could not delete cache: {error}",
                    error=e,
                ))
        else:
            self.statusBar().showMessage(_tr("status.cache_missing"))

    def _on_phase1_ready(self, cache: BreedingCache):
        """Ancestry computed — push to table and Safe Breeding so they're usable immediately."""
        self._breeding_cache = cache
        self._source_model.set_breeding_cache(cache)
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.set_cache(cache)
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.set_cache(cache)
        self._cache_progress.setFormat(_tr("loading.cache.pair_risks"))

    def _on_cache_ready(self, cache: BreedingCache):
        self._breeding_cache = cache
        self._cache_worker = None
        self._cache_progress.hide()
        # Push completed cache (now includes pairwise risk) to all views
        self._source_model.set_breeding_cache(cache)
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.set_cache(cache)
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.set_cache(cache)
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.set_cache(cache)
        self.statusBar().showMessage(
            self.statusBar().currentMessage() + _tr("status.cache_ready_suffix", default="  |  Breeding cache ready")
        )

    # ── Loading ────────────────────────────────────────────────────────────

    def load_save(self, path: str):
        self._current_save = path
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        self._watcher.addPath(path)

        # Cancel any in-progress load
        if self._save_load_worker is not None:
            self._save_load_worker.quit()
            self._save_load_worker.wait(500)
            self._save_load_worker = None

        # Show overlay while parsing (background thread — main thread stays responsive for repaint)
        name = os.path.basename(path)
        self._loading_label.setText(_tr("loading.save_named", name=name))
        overlay = self._loading_overlay
        parent = overlay.parentWidget()
        if parent:
            overlay.setGeometry(0, 0, parent.width(), parent.height())
        overlay.raise_()
        overlay.show()

        worker = SaveLoadWorker(path, parent=self)
        worker.finished_load.connect(self._on_save_loaded)
        self._save_load_worker = worker
        worker.start()

    def _on_save_loaded(self, result: dict):
        self._save_load_worker = None
        # Dismiss overlay immediately — UI work below is fast (model.load is O(n), no ancestry)
        self._loading_overlay.hide()
        self._save_view_disabled = True
        try:
            cats = result["cats"]
            errors = result["errors"]
            applied_overrides = result["applied_overrides"]
            override_rows = result["override_rows"]
            cal_explicit = result["cal_explicit"]
            cal_token = result["cal_token"]
            cal_rows = result["cal_rows"]

            self._cats = cats
            self._source_model.load(cats)
            self._rebuild_room_buttons(cats)
            self._refresh_filter_button_counts()
            self._filter(None, self._btn_all)
            # Only push cats to currently visible views immediately.
            # Hidden views call set_cats themselves when shown via _show_* methods.
            if self._tree_view is not None and self._tree_view.isVisible():
                self._tree_view.set_cats(cats)
            if self._safe_breeding_view is not None and self._safe_breeding_view.isVisible():
                self._safe_breeding_view.set_cats(cats)
            if self._breeding_partners_view is not None and self._breeding_partners_view.isVisible():
                self._breeding_partners_view.set_cats(cats)
            if self._room_optimizer_view is not None and self._room_optimizer_view.isVisible():
                self._room_optimizer_view.set_cats(cats)
            if self._perfect_planner_view is not None and self._perfect_planner_view.isVisible():
                self._perfect_planner_view.set_cats(cats)
            if self._calibration_view is not None and self._calibration_view.isVisible():
                self._calibration_view.set_context(self._current_save, cats)
            name = os.path.basename(self._current_save)
            self._save_lbl.setText(name)
            self.setWindowTitle(_tr("app.title_with_save", name=name))

            msg = _tr(
                "status.save_loaded",
                default="Loaded {count} cats from {name}",
                count=len(cats),
                name=name,
            )
            if errors:
                msg += _tr(
                    "status.save_loaded.parse_errors_suffix",
                    default="  ({count} parse errors)",
                    count=len(errors),
                )
            if applied_overrides:
                msg += _tr(
                    "status.save_loaded.gender_overrides_suffix",
                    default="  ({applied}/{rows} gender overrides)",
                    applied=applied_overrides,
                    rows=override_rows,
                )
            if cal_rows:
                msg += _tr(
                    "status.save_loaded.calibration_suffix",
                    default="  (calibration: {explicit} explicit, {token} token)",
                    explicit=cal_explicit,
                    token=cal_token,
                )
            self.statusBar().showMessage(msg)

            # Start background breeding cache computation
            self._start_breeding_cache(cats)

            # Update default save menu items
            self._update_default_save_menu()
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            self.statusBar().showMessage(_tr(
                "status.save_load_failed",
                default="Error loading save: {error}",
                error=e,
            ))
        finally:
            self._save_view_disabled = False
            self._restore_current_view()

    def _update_default_save_menu(self):
        """Update the enabled state of default save menu items."""
        has_save = self._current_save is not None
        default_save = _saved_default_save()
        is_current_default = has_save and default_save == self._current_save

        self._set_default_save_action.setEnabled(has_save and not is_current_default)
        self._clear_default_save_action.setEnabled(has_save and is_current_default)

    def _set_current_as_default(self):
        """Set the current save file as the default."""
        if self._current_save:
            _set_default_save(self._current_save)
            name = os.path.basename(self._current_save)
            self.statusBar().showMessage(_tr(
                "status.default_save_set",
                default="Default save set to: {name}",
                name=name,
            ))
            self._update_default_save_menu()

    def _clear_default_save(self):
        """Clear the default save setting."""
        _set_default_save(None)
        self.statusBar().showMessage(_tr("status.default_save_cleared", default="Default save cleared"))
        self._update_default_save_menu()

    def _toggle_lineage(self, checked: bool):
        self._show_lineage = checked
        for col in (COL_GEN_DEPTH, COL_SRC):
            self._table.setColumnHidden(col, not checked)
        self._source_model.set_show_lineage(checked)
        self._detail.set_show_lineage(checked)
        self._on_selection()   # refresh detail panel with updated flag

    def _open_file(self):
        saves   = find_save_files()
        start   = os.path.dirname(saves[0]) if saves else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self,
            _tr("dialog.open_save.title"),
            start,
            _tr("dialog.open_save.filter"),
        )
        if path:
            self.load_save(path)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_loading_overlay") and self._loading_overlay.isVisible():
            parent = self._loading_overlay.parentWidget()
            if parent:
                self._loading_overlay.setGeometry(0, 0, parent.width(), parent.height())

    def _reload(self):
        if self._current_save:
            self.load_save(self._current_save)

    def _on_file_changed(self, path: str):
        if path == self._current_save:
            self._reload()

    def _open_tree_browser(self):
        _save_current_view("tree")
        self._show_tree_view()
        rows = list({
            self._proxy_model.mapToSource(idx).row()
            for idx in self._table.selectionModel().selectedRows()
        })
        cats = [c for r in rows[:1] if (c := self._source_model.cat_at(r)) is not None]
        if cats and self._tree_view is not None:
            self._tree_view.select_cat(cats[0])

    def _open_safe_breeding_view(self):
        _save_current_view("safe_breeding")
        self._show_safe_breeding_view()
        rows = list({
            self._proxy_model.mapToSource(idx).row()
            for idx in self._table.selectionModel().selectedRows()
        })
        cats = [c for r in rows[:1] if (c := self._source_model.cat_at(r)) is not None]
        if cats and self._safe_breeding_view is not None:
            self._safe_breeding_view.select_cat(cats[0])

    def _open_breeding_partners_view(self):
        _save_current_view("breeding_partners")
        self._show_breeding_partners_view()

    def _open_room_optimizer(self):
        _save_current_view("room_optimizer")
        self._show_room_optimizer_view()

    def _open_perfect_planner_view(self):
        _save_current_view("perfect_planner")
        self._show_perfect_planner_view()

    def _open_calibration_view(self):
        _save_current_view("calibration")
        self._show_calibration_view()

    def _open_mutation_planner_view(self):
        _save_current_view("mutation_planner")
        self._show_mutation_planner_view()

    def _restore_current_view(self):
        """Restore the last-used view after a save is loaded."""
        view = _load_current_view()
        _restore_map = {
            "tree":               self._show_tree_view,
            "safe_breeding":      self._show_safe_breeding_view,
            "breeding_partners":  self._show_breeding_partners_view,
            "room_optimizer":     self._show_room_optimizer_view,
            "perfect_planner":    self._show_perfect_planner_view,
            "calibration":        self._show_calibration_view,
            "mutation_planner":   self._show_mutation_planner_view,
        }
        fn = _restore_map.get(view)
        if fn:
            fn()

    # ── UI zoom ───────────────────────────────────────────────────────────

    def _scaled(self, value: int) -> int:
        return max(1, round(value * (self._zoom_percent / 100.0)))

    def _update_zoom_info_action(self):
        if hasattr(self, "_zoom_info_action"):
            self._zoom_info_action.setText(_tr("menu.settings.zoom_info", percent=self._zoom_percent))

    def _set_zoom(self, percent: int):
        clamped = max(_ZOOM_MIN, min(_ZOOM_MAX, int(percent)))
        if clamped == self._zoom_percent:
            return
        self._zoom_percent = clamped
        self._apply_zoom()
        self._update_zoom_info_action()
        self.statusBar().showMessage(_tr(
            "status.zoom_changed",
            default="UI zoom set to {percent}%",
            percent=self._zoom_percent,
        ))

    def _change_zoom(self, direction: int):
        self._set_zoom(self._zoom_percent + (direction * _ZOOM_STEP))

    def _reset_zoom(self):
        self._set_zoom(100)

    def _change_font_size(self, direction: int):
        self._set_font_size_offset(self._font_size_offset + direction)

    def _set_font_size_offset(self, offset: int):
        clamped = max(-6, min(12, offset))
        if clamped == self._font_size_offset:
            return
        self._font_size_offset = clamped
        self._apply_zoom()
        self._update_font_size_info_action()
        label = _font_size_offset_label(clamped)
        self.statusBar().showMessage(_tr(
            "status.font_size_offset",
            default="Font size offset: {label}",
            label=label,
        ))

    def _update_font_size_info_action(self):
        if hasattr(self, "_font_size_info_action"):
            off = self._font_size_offset
            label = _font_size_offset_label(off)
            self._font_size_info_action.setText(_tr("menu.settings.font_size_info", label=label))

    def _apply_zoom(self):
        app = QApplication.instance()
        font = QFont(self._base_font)
        base_pt = self._base_font.pointSizeF()
        if base_pt > 0:
            zoomed_pt = base_pt * (self._zoom_percent / 100.0) + self._font_size_offset
            font.setPointSizeF(max(_ACCESSIBILITY_MIN_FONT_PT, zoomed_pt))
        elif self._base_font.pixelSize() > 0:
            font.setPixelSize(max(_ACCESSIBILITY_MIN_FONT_PX, self._scaled(self._base_font.pixelSize()) + self._font_size_offset))
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

        # Scale all hardcoded stylesheet font-size values across the whole window.
        # 1pt ≈ 1.33px; round to nearest integer pixel.
        offset_px = round(self._font_size_offset * 1.333)
        _apply_font_offset_to_tree(self, offset_px)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hsep() -> QFrame:
    f = QFrame(); f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color:#1e1e38; margin:6px 0;")
    return f


def _ensure_gpak_path_interactive(parent: Optional[QWidget] = None):
    if _GPAK_PATH:
        return

    if os.path.isdir(r"C:\Program Files (x86)\Steam\steamapps\common\Mewgenics"):
        start_dir = r"C:\Program Files (x86)\Steam\steamapps\common\Mewgenics"
    elif os.path.isdir(r"C:\Program Files\Steam\steamapps\common\Mewgenics"):
        start_dir = r"C:\Program Files\Steam\steamapps\common\Mewgenics"
    elif os.path.isdir(r"D:\Games\Mewgenics"):
        start_dir = r"D:\Games\Mewgenics"
    else:
        start_dir = str(Path.home())
    chosen_dir = QFileDialog.getExistingDirectory(
        parent,
        "Select Mewgenics Install Folder",
        start_dir,
    )
    if not chosen_dir:
        return

    gpak_path = os.path.join(chosen_dir, "resources.gpak")
    if os.path.exists(gpak_path):
        _set_gpak_path(gpak_path)
        return

    QMessageBox.warning(
        parent,
        "resources.gpak not found",
        "The selected folder does not contain resources.gpak. "
        "Choose the Mewgenics install directory that contains that file.",
    )


# ── Entry point ───────────────────────────────────────────────────────────────

class SaveSelectorDialog(QDialog):
    """Startup dialog for picking which save file to load."""

    def __init__(self, saves: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{_tr('app.title')} — {_tr('save_picker.title')}")
        self.setFixedSize(520, 360)
        self.setStyleSheet(
            "QDialog { background:#0d0d1c; }"
            "QLabel { color:#ccc; }"
            "QListWidget { background:#101023; color:#ddd; border:1px solid #26264a;"
            " font-size:13px; }"
            "QListWidget::item { padding:6px; }"
            "QListWidget::item:selected { background:#1e3060; }"
            "QPushButton { background:#1f5f4a; color:#f2f7f3; border:1px solid #3f8f72;"
            " border-radius:4px; padding:8px 20px; font-size:12px; font-weight:bold; }"
            "QPushButton:hover { background:#26735a; }"
            "QPushButton:disabled { background:#1a1a32; color:#555; border-color:#2a2a4a; }"
        )
        self._selected_path: Optional[str] = None

        vb = QVBoxLayout(self)
        vb.setContentsMargins(16, 16, 16, 16)
        vb.setSpacing(12)

        title = QLabel(_tr("save_picker.title"))
        title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
        vb.addWidget(title)

        self._list = QListWidget()
        for path in saves:
            name = os.path.basename(path)
            folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
            mtime = os.path.getmtime(path)
            ts = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            item = QListWidgetItem(f"{name}  ({folder})  —  {ts}")
            item.setData(Qt.UserRole, path)
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _: self._accept())
        vb.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._open_btn = QPushButton(_tr("save_picker.open"))
        self._open_btn.clicked.connect(self._accept)
        self._open_btn.setEnabled(len(saves) > 0)
        btn_row.addWidget(self._open_btn)

        browse_btn = QPushButton(_tr("save_picker.browse"))
        browse_btn.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        browse_btn.clicked.connect(self._browse)
        btn_row.addWidget(browse_btn)
        vb.addLayout(btn_row)

    def _accept(self):
        cur = self._list.currentItem()
        if cur is not None:
            self._selected_path = cur.data(Qt.UserRole)
            self.accept()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            _tr("dialog.open_save.title"),
            str(Path.home()),
            _tr("dialog.open_save.filter"),
        )
        if path:
            self._selected_path = path
            self.accept()

    @property
    def selected_path(self) -> Optional[str]:
        return self._selected_path


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

    if not _GPAK_PATH:
        QMessageBox.information(
            None,
            "Locate Mewgenics",
            "Ability and mutation descriptions need the game's resources.gpak.\n"
            "Select your Mewgenics install folder to enable them.",
        )
        _ensure_gpak_path_interactive()

    # Check for default save first
    default_save = _saved_default_save()
    initial_save: Optional[str] = default_save

    # Only show save selector if no default save is set
    if not default_save:
        saves = find_save_files()
        if saves:
            dlg = SaveSelectorDialog(saves)
            if dlg.exec() == QDialog.Accepted:
                initial_save = dlg.selected_path
            else:
                return 0  # User cancelled

    win = MainWindow(initial_save=initial_save)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
