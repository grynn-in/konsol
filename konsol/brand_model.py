"""The site's brand colour: parse, check, derive (konsol#415).

Deepak Pai, 7 Nov 2026: "lets go with : Teal-green #0F766E (my pick)". Rejected:
Deep green #0F5132, Forest #166534 (too close to the green "ready" status), and
a colour fixed in code. Each site sets its own in Close Settings; #0F766E is the
declared default. Pure: no frappe, so the host tests run it directly.
"""
import re

DEFAULT = "#0F766E"
#: WCAG 2.1 AA for normal text: white button labels must stay readable.
MIN_CONTRAST = 4.5

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_WHITE = (255, 255, 255)
_BLACK = (0, 0, 0)
#: Frappe v16's dark Desk surface, which the dark tint is mixed toward.
_DARK_SURFACE = (23, 23, 23)


def normalise(value):
    """``#RRGGBB`` in upper case, or ValueError naming the value."""
    text = (value or "").strip()
    match = _HEX.match(text)
    if not match:
        raise ValueError(f"Brand Colour {text!r} is not a hex colour such as #0F766E.")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return "#" + digits.upper()


def _rgb(value):
    h = normalise(value)
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb):
    return "#" + "".join(f"{round(max(0, min(255, c))):02X}" for c in rgb)


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_on_white(value):
    """WCAG 2.1 contrast ratio of white text on this colour."""
    return 1.05 / (_luminance(_rgb(value)) + 0.05)


def validate(value):
    """Refuse a colour white text cannot be read on; return it normalised."""
    colour = normalise(value)
    ratio = contrast_on_white(colour)
    if ratio < MIN_CONTRAST:
        raise ValueError(
            f"Brand Colour {colour} gives white text a contrast of {ratio:.2f} : 1; "
            f"buttons need at least {MIN_CONTRAST} : 1 (WCAG AA). Choose a darker shade."
        )
    return colour


def palette(value):
    """The shades the Desk uses, all derived from the one colour."""
    rgb = _rgb(validate(value))
    return {
        "primary": _hex(rgb),
        "strong": _hex(_mix(rgb, _BLACK, 0.22)),
        "tint": _hex(_mix(rgb, _WHITE, 0.9)),
        "strong_dark": _hex(_mix(rgb, _WHITE, 0.45)),
        "tint_dark": _hex(_mix(rgb, _DARK_SURFACE, 0.75)),
    }
