"""Shared visual language: colours, geometry, and cairo helpers.

Kept in one place so the tray icon and the popup panel stay in sync.
Colours are (r, g, b) floats in 0..1; alpha is always passed separately at
the call site so the same hue can be used at different opacities.
"""

from __future__ import annotations

import math

# -- battery level palette ---------------------------------------------------

GOOD = (0.29, 0.87, 0.50)      # #4ADE80
MEDIUM = (0.98, 0.75, 0.14)    # #FBBF24
LOW = (0.97, 0.44, 0.44)       # #F87171
CHARGING = (0.22, 0.74, 0.97)  # #38BDF8
UNKNOWN = (0.58, 0.64, 0.72)   # #94A3B8

# Thresholds at which the colour changes.
MEDIUM_BELOW = 50
LOW_BELOW = 21

# -- panel surface -----------------------------------------------------------

SURFACE_TOP = (0.094, 0.094, 0.114)     # #18181D
SURFACE_BOTTOM = (0.059, 0.059, 0.075)  # #0F0F13
SURFACE_ALPHA_TOP = 0.72
SURFACE_ALPHA_BOTTOM = 0.84

BORDER = (1.0, 1.0, 1.0)
BORDER_ALPHA = 0.17
HIGHLIGHT_ALPHA = 0.10  # inner top edge, sells the "glass" look

TRACK = (1.0, 1.0, 1.0)
TRACK_ALPHA = 0.13

TEXT_PRIMARY = "#F5F5F7"
TEXT_SECONDARY = "rgba(245, 245, 247, 0.55)"
TEXT_FAINT = "rgba(245, 245, 247, 0.38)"

# -- geometry ----------------------------------------------------------------

PANEL_RADIUS = 16
# Wide enough that a row with a Connect button still fits, so the panel does
# not change width when a device goes offline while it is open.
PANEL_WIDTH = 368
SHADOW_MARGIN = 20  # transparent gutter the drop shadow is painted into
RING_SIZE = 46
CELL_RING_SIZE = 36  # one cell of a multi-part device (earbud, case)
RING_THICKNESS = 4.0


def level_color(percent: int | None,
                charging: bool = False) -> tuple[float, float, float]:
    """Pick the accent colour for a battery reading."""
    if charging:
        return CHARGING
    if percent is None:
        return UNKNOWN
    if percent < LOW_BELOW:
        return LOW
    if percent < MEDIUM_BELOW:
        return MEDIUM
    return GOOD


def rounded_rect(cr, x: float, y: float, width: float, height: float,
                 radius: float) -> None:
    """Append a rounded-rectangle path to the cairo context."""
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_shadow(cr, x: float, y: float, width: float, height: float,
                radius: float, spread: int = SHADOW_MARGIN) -> None:
    """Fake a soft drop shadow with stacked translucent rounded rects.

    X11 gives an undecorated popup no compositor shadow of its own, so we
    paint one. Stacking cheap strokes avoids pulling in a blur dependency;
    the alpha falls off quadratically which reads as a soft penumbra.
    """
    for step in range(spread, 0, -1):
        ratio = step / spread
        alpha = 0.16 * (1.0 - ratio) ** 2
        if alpha <= 0.001:
            continue
        cr.set_source_rgba(0, 0, 0, alpha)
        rounded_rect(cr, x - step, y - step + step * 0.35,
                     width + step * 2, height + step * 2, radius + step)
        cr.fill()


def bolt_path(cr, cx: float, cy: float, size: float) -> None:
    """Append a lightning-bolt path centred on (cx, cy), fitting `size`."""
    unit = size / 2.0
    # Normalised bolt outline, y grows downward.
    points = [
        (0.16, -1.00), (-0.60, 0.14), (-0.08, 0.14),
        (-0.16, 1.00), (0.60, -0.14), (0.08, -0.14),
    ]
    cr.new_sub_path()
    cr.move_to(cx + points[0][0] * unit, cy + points[0][1] * unit)
    for px, py in points[1:]:
        cr.line_to(cx + px * unit, cy + py * unit)
    cr.close_path()


def draw_bolt(cr, cx: float, cy: float, size: float,
              color: tuple[float, float, float], alpha: float = 1.0) -> None:
    """Fill a lightning bolt centred on (cx, cy)."""
    cr.save()
    cr.set_source_rgba(*color, alpha)
    bolt_path(cr, cx, cy, size)
    cr.fill()
    cr.restore()
