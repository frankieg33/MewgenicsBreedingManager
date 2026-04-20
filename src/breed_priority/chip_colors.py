"""Chip color mapping for the Breed Priority view.

Maps values (scores, stat counts, ratings, weights) to (bg, fg) color pairs
for rendering pill-style chips in the score table.
"""

from .color_utils import ColorUtils


class ChipColors:
    """Maps values to (bg, fg) chip color pairs."""

    @staticmethod
    def rarity(n: int, threshold: float = 7.0) -> tuple:
        """Return (bg, fg) chip colors for a stat-at-7 chip.

        n <= threshold        → full green  (within scoring range, full points)
        n >= threshold + 10   → full brown  (very common, no score contribution)
        Values in between fade linearly from green to brown.
        """
        from .theme import _CHIP_DESIRABLE, _CHIP_RARITY_DIM
        t = min(1.0, max(0.0, (n - threshold) / 10.0))
        return (
            ColorUtils.lerp(_CHIP_DESIRABLE[0], _CHIP_RARITY_DIM[0], t),
            ColorUtils.lerp(_CHIP_DESIRABLE[1], _CHIP_RARITY_DIM[1], t),
        )

    @staticmethod
    def sevens(count_7: int, max_7: int, positive_weight: bool) -> str:
        """Return gradient color for a cat by count of stats at 7.

        With positive_weight=True:  0→red, max_7→green, midpoint→yellow
        With positive_weight=False: reversed (0→green, max_7→red)
        """
        from .theme import (
            CLR_TEXT_GRAYEDOUT, CLR_DESIRABLE, _CLR_RED, _CLR_YELLOW,
        )
        if max_7 == 0:
            return CLR_TEXT_GRAYEDOUT
        lo, hi = (_CLR_RED, CLR_DESIRABLE) if positive_weight else (CLR_DESIRABLE, _CLR_RED)
        t = count_7 / max_7  # 0.0 → 1.0
        if t <= 0.5:
            return ColorUtils.lerp(lo, _CLR_YELLOW, t * 2)
        else:
            return ColorUtils.lerp(_CLR_YELLOW, hi, (t - 0.5) * 2)

    @staticmethod
    def rank(score_map: dict) -> dict:
        """Map categorical labels to display colors by relative rank.

        score_map: {label: score_value}

        Rules:
          - 3 distinct values: highest=green, middle=grey, lowest=red
          - 2 distinct values: highest=green, lower=grey  (no red - tied pair)
          - 1 distinct value : all grey  (3-way tie)
        """
        from .theme import (
            CLR_DESIRABLE, CLR_UNDESIRABLE, CLR_VALUE_NEUTRAL,
        )
        unique = sorted(set(score_map.values()), reverse=True)
        result = {}
        for label, score in score_map.items():
            if len(unique) == 1:
                result[label] = CLR_VALUE_NEUTRAL
            elif len(unique) == 2:
                result[label] = CLR_DESIRABLE if score == unique[0] else CLR_VALUE_NEUTRAL
            else:
                if score == unique[0]:
                    result[label] = CLR_DESIRABLE
                elif score == unique[-1]:
                    result[label] = CLR_UNDESIRABLE
                else:
                    result[label] = CLR_VALUE_NEUTRAL
        return result

    @staticmethod
    def paired_weights(w_a: float, w_b: float) -> tuple:
        """Return (color_a, color_b) for two related weights shown side-by-side.

        Rules:
          Both positive, equal   -> both green
          Both positive, unequal -> greater=green, lesser=yellow
          Both negative, equal   -> both red
          Both negative, unequal -> greater (less negative)=yellow, lesser=red
          Mixed signs            -> positive=green, negative=red
          Zero                   -> grey (no preference expressed)
        """
        from .theme import (
            CLR_DESIRABLE, CLR_NEUTRAL, CLR_UNDESIRABLE, CLR_VALUE_NEUTRAL,
        )
        def _sign(v): return 1 if v > 0 else (-1 if v < 0 else 0)
        sa, sb = _sign(w_a), _sign(w_b)
        if sa == 0 and sb == 0:
            return CLR_VALUE_NEUTRAL, CLR_VALUE_NEUTRAL
        if sa > 0 and sb > 0:
            if w_a > w_b: return CLR_DESIRABLE, CLR_NEUTRAL
            if w_b > w_a: return CLR_NEUTRAL,   CLR_DESIRABLE
            return CLR_DESIRABLE, CLR_DESIRABLE
        if sa < 0 and sb < 0:
            if w_a > w_b: return CLR_NEUTRAL,     CLR_UNDESIRABLE
            if w_b > w_a: return CLR_UNDESIRABLE, CLR_NEUTRAL
            return CLR_UNDESIRABLE, CLR_UNDESIRABLE
        # mixed signs or one is zero
        clr_a = CLR_DESIRABLE if sa > 0 else (CLR_UNDESIRABLE if sa < 0 else CLR_VALUE_NEUTRAL)
        clr_b = CLR_DESIRABLE if sb > 0 else (CLR_UNDESIRABLE if sb < 0 else CLR_VALUE_NEUTRAL)
        return clr_a, clr_b

    @staticmethod
    def sex_indicator(color: str) -> tuple:
        """Map an indicator color string to a (bg, fg) chip pair."""
        from .theme import (
            CLR_DESIRABLE, CLR_UNDESIRABLE, CLR_NEUTRAL,
            CLR_TEXT_GRAYEDOUT, CLR_TEXT_SECONDARY,
            _CHIP_DESIRABLE, _CHIP_UNDESIRABLE, _CHIP_NEUTRAL,
        )
        if color == CLR_DESIRABLE:
            return _CHIP_DESIRABLE
        if color == CLR_UNDESIRABLE:
            return _CHIP_UNDESIRABLE
        if color == CLR_NEUTRAL:
            return _CHIP_NEUTRAL
        return (CLR_TEXT_GRAYEDOUT, CLR_TEXT_SECONDARY)

    @staticmethod
    def stat_ranked(t: float) -> tuple:
        """Return (bg, fg) chip pair for a stat value given its percentile rank t ∈ [0, 1].

        t=0 → dim purple-grey (column minimum rank)
        t=1 → bright teal (column maximum rank)

        Rank is computed over the column's unique values so outliers only set
        the top anchor — they do not compress the colors of other values.
        bg is derived from fg for readable contrast on the dark surface.
        """
        from .theme import (
            _CLR_STAT_DYNAMIC_LOW, _CLR_STAT_DYNAMIC_HIGH,
            CLR_SURFACE_SCORE_AREA,
        )
        fg = ColorUtils.lerp(_CLR_STAT_DYNAMIC_LOW, _CLR_STAT_DYNAMIC_HIGH, t)
        bg = ColorUtils.derive_chip_bg(fg, CLR_SURFACE_SCORE_AREA)
        return bg, fg

    @staticmethod
    def stat_col_ranks(values: list) -> dict:
        """Build a value→percentile-rank mapping for a single stat column.

        Ranks are assigned over unique values only, so repeated values share
        a rank and outliers do not pull the gradient away from the bulk.

        Returns {value: t} where t ∈ [0, 1] (0=lowest, 1=highest unique value).
        When only one unique value exists every entry maps to 1.0.
        """
        unique = sorted(set(values))
        n = len(unique)
        if n <= 1:
            return {v: 1.0 for v in unique}
        return {v: i / (n - 1) for i, v in enumerate(unique)}

    @staticmethod
    def from_score(score_val: float) -> tuple:
        """Map a score value to a (bg, fg) chip pair based on sign."""
        from .theme import _CHIP_DESIRABLE, _CHIP_UNDESIRABLE, _CHIP_DIM
        if score_val > 0:
            return _CHIP_DESIRABLE
        if score_val < 0:
            return _CHIP_UNDESIRABLE
        return _CHIP_DIM
