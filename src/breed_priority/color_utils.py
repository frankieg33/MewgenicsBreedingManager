"""Color math utilities for the Breed Priority view.

Pure functions for hex color interpolation, parsing, and blending.
No Qt dependencies.
"""


class ColorUtils:
    """Hex color math — interpolation, parsing, blending."""

    @staticmethod
    def lerp(c1: str, c2: str, t: float) -> str:
        """Linearly interpolate between two hex colors (#rrggbb). t clamped to [0,1]."""
        t = max(0.0, min(1.0, t))
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        return "#{:02x}{:02x}{:02x}".format(
            int(r1 + (r2 - r1) * t),
            int(g1 + (g2 - g1) * t),
            int(b1 + (b2 - b1) * t),
        )

    @staticmethod
    def lerp_step(c1: str, c2: str, total_steps: int, step: int) -> str:
        """Return interpolated color at *step* within a [1..total_steps] range.

        step=1 returns c1, step=total_steps returns c2.
        """
        if total_steps <= 1:
            return c2
        t = (step - 1) / (total_steps - 1)
        return ColorUtils.lerp(c1, c2, t)

    @staticmethod
    def parse_hex(c: str) -> tuple:
        """Parse a #rrggbb string to (r, g, b) integers."""
        return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)

    @staticmethod
    def blend(c1: str, c2: str, t: float) -> str:
        """Blend c1 toward c2 by factor t (0=c1, 1=c2)."""
        return ColorUtils.lerp(c1, c2, t)

    @staticmethod
    def derive_chip_bg(fg: str, theme_dark: str) -> str:
        """Derive a dark pill/chip background from a foreground color.

        Blends fg into theme_dark at 14% strength, producing a background that
        carries the hue of the foreground without competing with the text.
        """
        return ColorUtils.blend(theme_dark, fg, 0.14)

    @staticmethod
    def with_saturation(c: str, saturation: float, min_value: float = 0.0) -> str:
        """Return hex color with HSV saturation set to `saturation` [0,1].

        Hue is preserved. Value is preserved but clamped to at least `min_value`.
        """
        r, g, b = ColorUtils.parse_hex(c)
        rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
        cmax = max(rf, gf, bf)
        cmin = min(rf, gf, bf)
        delta = cmax - cmin

        if delta == 0:
            hue = 0.0
        elif cmax == rf:
            hue = 60.0 * (((gf - bf) / delta) % 6)
        elif cmax == gf:
            hue = 60.0 * ((bf - rf) / delta + 2)
        else:
            hue = 60.0 * ((rf - gf) / delta + 4)

        value = max(cmax, min_value)
        chroma = value * saturation
        x = chroma * (1 - abs((hue / 60) % 2 - 1))
        m = value - chroma

        sector = int(hue / 60) % 6
        if sector == 0:
            r1, g1, b1 = chroma, x, 0.0
        elif sector == 1:
            r1, g1, b1 = x, chroma, 0.0
        elif sector == 2:
            r1, g1, b1 = 0.0, chroma, x
        elif sector == 3:
            r1, g1, b1 = 0.0, x, chroma
        elif sector == 4:
            r1, g1, b1 = x, 0.0, chroma
        else:
            r1, g1, b1 = chroma, 0.0, x

        return "#{:02x}{:02x}{:02x}".format(
            int((r1 + m) * 255),
            int((g1 + m) * 255),
            int((b1 + m) * 255),
        )
