"""Tray icon rendering.

XAppStatusIcon takes either a themed icon name or an absolute file path, and
no icon theme ships a 37%-full battery, so we draw our own with cairo and
hand over a PNG path. Results are cached on disk keyed by everything that
affects the pixels, so a steady battery level costs one render ever.
"""

from __future__ import annotations

import os
import tempfile

import cairo

from . import theme

CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "voltaic", "icons",
)

# Outline colour. Cinnamon panels are dark by default; a near-white stroke at
# partial alpha reads cleanly on dark and stays visible on mid-grey.
OUTLINE = (0.96, 0.96, 0.98)
OUTLINE_ALPHA = 0.92


def _draw_icon(cr: cairo.Context, size: int, percent: int | None,
               charging: bool) -> None:
    # A battery reads as a battery at 22px only if it stays rectangular —
    # a large corner radius turns it into an anonymous pill.
    body_w = size * 0.80
    body_h = size * 0.46
    nub_w = size * 0.055
    nub_h = body_h * 0.46

    x = (size - body_w - nub_w) / 2.0
    y = round((size - body_h) / 2.0)
    stroke = max(1.0, round(size * 0.062))
    radius = max(1.0, size * 0.07)

    cr.set_line_width(stroke)
    cr.set_source_rgba(*OUTLINE, OUTLINE_ALPHA)
    theme.rounded_rect(cr, x + stroke / 2, y + stroke / 2,
                       body_w - stroke, body_h - stroke, radius)
    cr.stroke()

    # Terminal nub on the right.
    theme.rounded_rect(cr, x + body_w, y + (body_h - nub_h) / 2.0,
                       nub_w, nub_h, nub_w / 2.5)
    cr.fill()

    # Fill proportional to charge, hugging the inside of the outline.
    inset = stroke + max(1.0, size * 0.045)
    track_x = x + inset
    track_y = y + inset
    track_w = body_w - inset * 2
    track_h = body_h - inset * 2
    fill_radius = min(radius * 0.5, track_h / 2)

    if percent is None:
        # Unknown: a single dash rather than a misleading empty battery.
        cr.set_source_rgba(*theme.UNKNOWN, 0.85)
        dash_w = track_w * 0.5
        theme.rounded_rect(cr, track_x + (track_w - dash_w) / 2,
                           track_y + track_h / 2 - stroke * 0.4,
                           dash_w, stroke * 0.8, stroke * 0.4)
        cr.fill()
        return

    fill_w = track_w * max(0.0, min(100, percent)) / 100.0
    if fill_w > 0.4:
        cr.set_source_rgba(*theme.level_color(percent, charging), 1.0)
        theme.rounded_rect(cr, track_x, track_y, max(fill_w, stroke * 1.2),
                           track_h, fill_radius)
        cr.fill()

    if charging:
        # The bolt has to stay readable over both the filled and the empty
        # part of the battery, so it is drawn white over a dark outline
        # rather than punched out of the fill.
        bolt_size = body_h * 0.74
        cr.save()
        # Clip to the battery interior so the bolt's dark outline can never
        # bleed past the body and blur the silhouette.
        theme.rounded_rect(cr, x + stroke, y + stroke,
                           body_w - stroke * 2, body_h - stroke * 2,
                           max(0.5, radius - stroke * 0.5))
        cr.clip()
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_width(max(1.0, size * 0.058))
        cr.set_source_rgba(0.04, 0.05, 0.07, 0.72)
        theme.bolt_path(cr, size / 2.0, y + body_h / 2.0, bolt_size)
        cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.97)
        theme.bolt_path(cr, size / 2.0, y + body_h / 2.0, bolt_size)
        cr.fill()
        cr.restore()


def render_icon(percent: int | None, charging: bool = False,
                size: int = 22) -> str:
    """Render the tray icon and return a PNG path, using the disk cache."""
    size = max(16, int(size))
    key = f"batt-{size}-{'na' if percent is None else int(percent)}-{int(charging)}.png"
    path = os.path.join(CACHE_DIR, key)
    if os.path.exists(path):
        return path

    os.makedirs(CACHE_DIR, exist_ok=True)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    _draw_icon(cr, size, percent, charging)
    surface.flush()

    # Write via a temp file in the same directory so a concurrent reader
    # never sees a half-written PNG.
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".png")
    os.close(fd)
    surface.write_to_png(tmp)
    os.replace(tmp, path)
    return path
