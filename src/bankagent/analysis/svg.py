"""Dependency-free SVG tornado chart for `docs/evidence/roi_tornado.svg`."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from html import escape

from bankagent.analysis.model import Bar

WIDTH = 900
LABEL_W = 400
RIGHT_PAD = 40
TOP = 70
ROW_H = 34
BAR_H = 20
LOW_COLOR = "#2b6cb0"  # input at its low value
HIGH_COLOR = "#dd6b20"  # input at its high value
INK = "#1a202c"
MUTED = "#4a5568"
GRID = "#e2e8f0"


def _nice_step(span: float, target_ticks: int = 5) -> float:
    raw = span / target_ticks
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        if raw <= factor * magnitude:
            return factor * magnitude
    return 10 * magnitude


def tornado_svg(
    bars: Sequence[Bar],
    base: float,
    title: str,
    axis_label: str,
    display: Callable[[str, float], str],
    names: dict[str, str],
) -> str:
    """Horizontal tornado: each row swings one input from low (blue) to high (orange).

    `display(name, value)` formats an input value; `names` maps input names to row labels.
    """
    values = [base] + [v for b in bars for v in (b.savings_at_low, b.savings_at_high)]
    step = _nice_step((max(values) - min(values)) or 1.0)
    lo = math.floor(min(values) / step) * step
    hi = math.ceil(max(values) / step) * step
    plot_w = WIDTH - LABEL_W - RIGHT_PAD
    height = TOP + ROW_H * len(bars) + 70

    def x(value: float) -> float:
        return LABEL_W + (value - lo) / (hi - lo) * plot_w

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" font-family="Helvetica, Arial, sans-serif" '
        f'role="img" aria-label="{escape(title)}">',
        f"<title>{escape(title)}</title>",
        f'<rect width="{WIDTH}" height="{height}" fill="#ffffff"/>',
        f'<text x="16" y="28" font-size="17" font-weight="bold" fill="{INK}">'
        f"{escape(title)}</text>",
        f'<rect x="16" y="42" width="12" height="12" fill="{LOW_COLOR}"/>'
        f'<text x="34" y="52" font-size="12" fill="{MUTED}">input at its low value</text>'
        f'<rect x="190" y="42" width="12" height="12" fill="{HIGH_COLOR}"/>'
        f'<text x="208" y="52" font-size="12" fill="{MUTED}">input at its high value</text>',
    ]
    bottom = TOP + ROW_H * len(bars)
    tick = lo
    while tick <= hi + step / 2:
        tx = x(tick)
        out.append(
            f'<line x1="{tx:.1f}" y1="{TOP - 6}" x2="{tx:.1f}" y2="{bottom}" stroke="{GRID}"/>'
            f'<text x="{tx:.1f}" y="{bottom + 18}" font-size="11" fill="{MUTED}" '
            f'text-anchor="middle">{tick:,.2f}</text>'
        )
        tick += step
    for i, bar in enumerate(bars):
        y = TOP + i * ROW_H + (ROW_H - BAR_H) / 2
        s = bar.swing
        label = (
            f"{names.get(s.name, s.name)} ({display(s.name, s.low)} to {display(s.name, s.high)})"
        )
        out.append(
            f'<text x="{LABEL_W - 10}" y="{y + BAR_H / 2 + 4:.1f}" font-size="12" fill="{INK}" '
            f'text-anchor="end">{escape(label)}</text>'
        )
        # Draw the longer bar first so the shorter one stays visible when both share a side.
        pairs = sorted(
            ((bar.savings_at_low, LOW_COLOR), (bar.savings_at_high, HIGH_COLOR)),
            key=lambda p: -abs(p[0] - base),
        )
        for value, color in pairs:
            left, right = sorted((x(base), x(value)))
            out.append(
                f'<rect x="{left:.1f}" y="{y:.1f}" width="{max(right - left, 1):.1f}" '
                f'height="{BAR_H}" fill="{color}"/>'
            )
    bx = x(base)
    out += [
        f'<line x1="{bx:.1f}" y1="{TOP - 10}" x2="{bx:.1f}" y2="{bottom}" stroke="{INK}" '
        'stroke-width="1.5"/>',
        f'<text x="{bx:.1f}" y="{TOP - 14}" font-size="11" fill="{INK}" text-anchor="middle">'
        f"central {base:,.2f}</text>",
        f'<text x="{LABEL_W + plot_w / 2:.1f}" y="{bottom + 40}" font-size="12" fill="{MUTED}" '
        f'text-anchor="middle">{escape(axis_label)}</text>',
        "</svg>",
    ]
    return "\n".join(out) + "\n"
