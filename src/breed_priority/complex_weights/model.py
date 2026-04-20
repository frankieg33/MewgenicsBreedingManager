"""Complex Weight data model — conditions and weights for custom scoring rules.

No Qt dependencies. No imports from mewgenics_manager.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Field identifiers ─────────────────────────────────────────────────────────

FIELD_GENDER      = "gender"       # categorical: "m", "f", "?"
FIELD_LIBIDO      = "libido"       # categorical: "high", "normal", "low"
FIELD_AGGRESSION  = "aggression"   # categorical: "high", "normal", "low"
FIELD_SEXUALITY   = "sexuality"    # categorical: "gay", "bi", "straight"
FIELD_STAT_SUM    = "stat_sum"     # numeric: int
FIELD_AGE         = "age"          # numeric: int
FIELD_GENE_RISK   = "gene_risk"    # numeric: float (average in-scope risk %)
FIELD_GENE_UNIQUE = "gene_unique"  # boolean: True if gene risk == 0
FIELD_SCORE       = "score"        # numeric: float (total priority score)
FIELD_TRAIT       = "trait"        # multi-select: has any/all/none of chosen traits
# Individual stats use compound key "stat:STR", "stat:DEX", etc.
FIELD_STAT_PREFIX = "stat:"


# ── Categorical field option values ──────────────────────────────────────────

CATEGORICAL_VALUES = {
    FIELD_GENDER:     [("Male", "m"), ("Female", "f"), ("Unknown", "?")],
    FIELD_LIBIDO:     [("High", "high"), ("Normal", "normal"), ("Low", "low")],
    FIELD_AGGRESSION: [("High", "high"), ("Normal", "normal"), ("Low", "low")],
    FIELD_SEXUALITY:  [("Straight", "straight"), ("Bi", "bi"), ("Gay", "gay")],
}


# ── Operators ─────────────────────────────────────────────────────────────────

OP_EQ  = "=="
OP_NEQ = "!="
OP_GT  = ">"
OP_LT  = "<"
OP_GTE = ">="
OP_LTE = "<="

TRAIT_MODE_ANY  = "has_any"
TRAIT_MODE_ALL  = "has_all"
TRAIT_MODE_NONE = "has_none"

NUMERIC_OPS     = [OP_GT, OP_GTE, OP_LT, OP_LTE, OP_EQ, OP_NEQ]
CATEGORICAL_OPS = [OP_EQ, OP_NEQ]
TRAIT_OPS       = [TRAIT_MODE_ANY, TRAIT_MODE_NONE, TRAIT_MODE_ALL]

OP_DISPLAY = {
    OP_EQ:          "==",
    OP_NEQ:         "!=",
    OP_GT:          ">",
    OP_LT:          "<",
    OP_GTE:         ">=",
    OP_LTE:         "<=",
    TRAIT_MODE_ANY: "has any of",
    TRAIT_MODE_ALL: "has all of",
    TRAIT_MODE_NONE:"has none of",
}


# ── Logic ─────────────────────────────────────────────────────────────────────

LOGIC_AND = "AND"
LOGIC_OR  = "OR"


# ── Field display metadata ────────────────────────────────────────────────────

# (display_label, field_key) pairs in order for the editor field combobox.
# Stat entries are appended dynamically based on the game's stat list.
BASE_FIELD_OPTIONS = [
    ("Gender",             FIELD_GENDER),
    ("Libido",             FIELD_LIBIDO),
    ("Aggression",         FIELD_AGGRESSION),
    ("Sexuality",          FIELD_SEXUALITY),
    ("Stat Sum",           FIELD_STAT_SUM),
    ("Age",                FIELD_AGE),
    ("Gene Risk %",        FIELD_GENE_RISK),
    ("Genetically Unique", FIELD_GENE_UNIQUE),
    ("Score (total)",      FIELD_SCORE),
    ("Trait",              FIELD_TRAIT),
]


def build_field_options(stat_names: list) -> list:
    """Return full field options list including individual stats."""
    options = list(BASE_FIELD_OPTIONS)
    for sn in stat_names:
        options.append((f"Stat: {sn}", f"{FIELD_STAT_PREFIX}{sn}"))
    return options


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Condition:
    """A single predicate evaluated against a cat."""
    field: str
    operator: str
    value: Any  # str for categorical, float/int for numeric, list[str] for trait

    def to_dict(self) -> dict:
        return {"field": self.field, "operator": self.operator, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict) -> "Condition":
        return cls(
            field=d.get("field", FIELD_GENDER),
            operator=d.get("operator", OP_EQ),
            value=d.get("value", "m"),
        )


@dataclass
class ComplexWeight:
    """User-defined scoring rule: if conditions are met, add *delta* points."""
    name: str
    delta: float                              # score points when matched
    logic: str = LOGIC_AND                    # LOGIC_AND or LOGIC_OR
    conditions: list = field(default_factory=list)  # List[Condition]
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "name":       self.name,
            "delta":      self.delta,
            "logic":      self.logic,
            "conditions": [c.to_dict() for c in self.conditions],
            "enabled":    self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ComplexWeight":
        return cls(
            name=d.get("name", ""),
            delta=float(d.get("delta", 0.0)),
            logic=d.get("logic", LOGIC_AND),
            conditions=[Condition.from_dict(c) for c in d.get("conditions", [])],
            enabled=bool(d.get("enabled", True)),
        )
