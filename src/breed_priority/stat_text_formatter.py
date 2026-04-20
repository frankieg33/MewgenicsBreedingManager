"""Stat text formatting — emoji substitution and tooltip summary extraction.

Standalone module — no imports from mewgenics_manager to avoid circular deps.
"""

import re as _re


class StatTextFormatter:
    """Formats stat abbreviations, mutation tooltips, and ability tooltips into
    emoji-rich display strings."""

    EMOJI = {
        "STR": "💪", "str": "💪",
        "DEX": "🏹", "dex": "🏹",
        "CON": "🧡", "con": "🧡",
        "INT": "💡", "int": "💡",
        "SPD": "👟", "spd": "👟",
        "CHA": "👄", "cha": "👄",
        "LCK": "☘️", "lck": "☘️",
    }

    _STAT_NAMES = ("STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
                   "str", "dex", "con", "int", "spd", "cha", "lck")

    @staticmethod
    def _sort_parts(desc: str) -> str:
        """Sort comma-separated stat parts so positives come before negatives."""
        parts = [p.strip() for p in desc.split(",")]
        if len(parts) <= 1:
            return desc
        parts.sort(key=lambda p: 1 if p.lstrip().startswith("-") else 0)
        return ", ".join(parts)

    @staticmethod
    def _extract_english(text: str) -> str:
        """Extract only the English portion from a possibly multi-locale string."""
        if ".," in text:
            text = text.split(".,")[0]
        return text.rstrip(".")

    @classmethod
    def emojify(cls, desc: str) -> str:
        """Sort stat parts (positives first), replace stat abbreviations with emoji.

        e.g. '-1 LCK, +2 DEX' → '+2🏹, -1☘️'
             '+1 Holy Shield'  → '+1 Holy Shield'  (unchanged)
        """
        if not desc:
            return desc
        result = cls._sort_parts(desc)
        for name in cls._STAT_NAMES:
            if name in result:
                result = _re.sub(r'\b' + _re.escape(name) + r'\b', cls.EMOJI[name], result)
        result = _re.sub(r'(\+?-?\d+)\s+([^\x00-\x7F])', r'\1\2', result)
        return result

    @classmethod
    def mutation_summary(cls, tip: str) -> str:
        """Extract the effect line from a mutation tooltip.

        Mutation tooltips have the format:
            'Body Eyes Mutation (ID 300)\\nBody Mutation\\n+2 DEX, -1 LCK'
        or for non-numeric effects:
            'Docked Ears (ID 335)\\nDocked Ears\\nStart with Bleed 1 and a bonus attack'

        Strips the header (ID line) and mutation name, then returns the first
        remaining detail line (emojified).  Trailing "Affects:" lines are ignored.
        """
        if not tip:
            return ""
        lines = [ln.strip() for ln in tip.strip().split("\n") if ln.strip()]
        # Require at least: header (contains "(ID "), name, and one detail line
        if not lines or "(ID " not in lines[0] or len(lines) < 3:
            return ""
        detail_lines = lines[2:]
        while detail_lines and detail_lines[-1].startswith("Affects:"):
            detail_lines.pop()
        if not detail_lines:
            return ""
        return cls.emojify(cls._extract_english(detail_lines[0]))

    @classmethod
    def ability_summary(cls, tip: str) -> str:
        """Extract a short summary from an ability tooltip.

        Ability tips may be multi-line. First line is typically the short description.
        Replace stat names with emoji.
        """
        if not tip:
            return ""
        return cls.emojify(tip.strip().split("\n")[0].strip())
