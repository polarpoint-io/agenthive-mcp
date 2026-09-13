#!/usr/bin/env python3
"""Generate static/hero.svg.

Same visual family as polarpoint-io/agenthive's hero (near-black ground,
copper/amber accent, condensed-mono wordmark, "> " stat lines along the
foot) so the two repos read as one family, but the mark itself is
different: agenthive's is a honeycomb cell holding a team/agent/task
graph; this one is a single stdio pipe crossing the cell, carrying
message "packets" between a small IDE node and the hive node - this repo
is a thin bridge, not a store, so the mark should read as a wire, not a
graph.

Pure SVG, no headless-browser render step: safe for GitHub's markdown
image sanitizer (no embedded @font-face, no <script>), and there is no
raster step to keep byte-for-byte reproducible - the vector source *is*
the shipped asset.

Rendering is deterministic: packet placement comes from a seeded RNG, so
re-running this reproduces the same file byte for byte.

Usage:
    python3 hack/build_hero.py
"""
import math
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "hero.svg"

# ---- palette (same family as agenthive/helm-mirofish) ----------------------
BG_A = "#0a0e0f"
BG_B = "#0d1113"
AMBER_LT = "#f3c56b"
AMBER = "#e8a53d"
AMBER_MD = "#c98a2e"
AMBER_DK = "#8a5a1d"
AMBER_XD = "#4d3110"
MUTED = "#8f9498"
CELL_LINE = "#2a2420"

W, H = 2400, 960
CX, CY, HR = 560, 480, 300  # honeycomb center / cell radius
TX = 1030  # wordmark left edge

rnd = random.Random(23)


def hexagon(cx, cy, r, rot=0.0):
    pts = []
    for k in range(6):
        a = rot + k * math.pi / 3
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def hex_path(cx, cy, r, rot=0.0):
    pts = hexagon(cx, cy, r, rot)
    d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"
    return d


# ---- surrounding honeycomb (flat-top cells, axial layout) ------------------
cell_r = HR * 0.42
comb = []
for q in range(-3, 4):
    for s in range(-3, 4):
        x = CX + cell_r * 1.5 * q
        y = CY + cell_r * math.sqrt(3) * (s + q / 2)
        d = math.hypot(x - CX, y - CY)
        if d < HR * 0.98 or d > HR * 2.05:
            continue
        comb.append(
            f'<path d="{hex_path(x, y, cell_r * 0.94)}" fill="none" '
            f'stroke="{CELL_LINE}" stroke-width="2.5" opacity="{rnd.uniform(0.35, 0.7):.2f}"/>'
        )

# ---- the central cell: one stdio pipe, IDE node -> hive node --------------
central = hex_path(CX, CY, HR * 0.98)

# Endpoints sit near opposite rim of the cell, not at its center - this is
# a wire crossing the hive, not a graph anchored inside it.
ide = (CX - HR * 0.58, CY + HR * 0.06)
hive = (CX + HR * 0.6, CY - HR * 0.08)

# A gently bowed path reads as a "pipe" rather than a straight wire.
mid = ((ide[0] + hive[0]) / 2, (ide[1] + hive[1]) / 2 - HR * 0.22)
pipe_d = f"M {ide[0]:.1f},{ide[1]:.1f} Q {mid[0]:.1f},{mid[1]:.1f} {hive[0]:.1f},{hive[1]:.1f}"


def bezier_point(t):
    x = (1 - t) ** 2 * ide[0] + 2 * (1 - t) * t * mid[0] + t**2 * hive[0]
    y = (1 - t) ** 2 * ide[1] + 2 * (1 - t) * t * mid[1] + t**2 * hive[1]
    return x, y


packets = []
for i, t in enumerate(sorted(rnd.uniform(0.12, 0.88) for _ in range(6))):
    x, y = bezier_point(t)
    rr = rnd.uniform(7, 13)
    packets.append(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rr:.1f}" fill="{AMBER_LT}" '
        f'opacity="{rnd.uniform(0.55, 0.95):.2f}"/>'
    )

graph = [
    f'<path d="{pipe_d}" fill="none" stroke="url(#edge)" stroke-width="5" opacity=".9"/>',
]
graph += packets
graph += [
    # IDE endpoint: a small open square (a subprocess, not a stored node).
    f'<rect x="{ide[0]-20:.1f}" y="{ide[1]-20:.1f}" width="40" height="40" rx="6" '
    f'fill="{AMBER_XD}" stroke="{AMBER_LT}" stroke-width="3"/>',
    # Hive endpoint: the same lit knob style as agenthive's own anchor node.
    f'<circle cx="{hive[0]:.1f}" cy="{hive[1]:.1f}" r="24" fill="url(#knob)" opacity=".95"/>',
    f'<circle cx="{hive[0]:.1f}" cy="{hive[1]:.1f}" r="10.8" fill="{AMBER_LT}" opacity=".9"/>',
]

svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{BG_B}"/><stop offset="1" stop-color="{BG_A}"/>
    </linearGradient>
    <radialGradient id="glow" cx="24%" cy="46%" r="55%">
      <stop offset="0" stop-color="{AMBER}" stop-opacity=".14"/>
      <stop offset="1" stop-color="{AMBER}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="rim" x1="0" y1="0" x2="0.7" y2="1">
      <stop offset="0" stop-color="{AMBER_LT}"/>
      <stop offset=".45" stop-color="{AMBER}"/>
      <stop offset=".8" stop-color="{AMBER_DK}"/>
      <stop offset="1" stop-color="{AMBER_XD}"/>
    </linearGradient>
    <radialGradient id="knob" cx="35%" cy="30%" r="75%">
      <stop offset="0" stop-color="{AMBER_LT}"/><stop offset="1" stop-color="{AMBER_DK}"/>
    </radialGradient>
    <radialGradient id="cell" cx="34%" cy="26%" r="90%">
      <stop offset="0" stop-color="#1c1712"/><stop offset="1" stop-color="#090a0a"/>
    </radialGradient>
    <linearGradient id="edge" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{AMBER_LT}"/><stop offset="1" stop-color="{AMBER_DK}"/>
    </linearGradient>
    <clipPath id="cellClip"><path d="{central}"/></clipPath>
  </defs>

  <rect width="{W}" height="{H}" fill="url(#bg)"/>
  <rect width="{W}" height="{H}" fill="url(#glow)"/>

  <!-- ============================ the mark ============================ -->
  <g>
    {''.join(comb)}
    <path d="{central}" fill="url(#cell)" stroke="url(#rim)" stroke-width="{HR*0.05:.1f}"/>
    <path d="{hex_path(CX, CY, HR*1.015)}" fill="none" stroke="{AMBER_LT}" stroke-width="2" opacity=".35"/>
    <g clip-path="url(#cellClip)">
      {''.join(graph)}
    </g>
  </g>

  <!-- ============================ wordmark ============================ -->
  <text x="{TX}" y="428" font-family="Consolas, 'Courier New', monospace" font-weight="700"
        font-size="104" letter-spacing="4" fill="#ffffff">AGENTHIVE-MCP</text>
  <rect x="{TX}" y="468" width="1330" height="5" fill="{AMBER_MD}"/>
  <text x="{TX}" y="540" font-family="Consolas, 'Courier New', monospace" font-weight="500"
        font-size="40" letter-spacing="1" fill="{AMBER_MD}">MCP bridge for Cursor &amp; Claude Code</text>

  <!-- ============================ footer ============================== -->
  <g font-family="Consolas, 'Courier New', monospace" font-weight="400" font-size="34" fill="{MUTED}">
    <text x="86" y="820">&gt; stdio subprocess, no server of its own - reads AGENTHIVE_URL/TOKEN/TEAM_ID</text>
    <text x="86" y="868">&gt; member-scoped tools only - retrieve/log/agents/tasks, nothing admin-only</text>
    <text x="86" y="916">&gt; the approval gate is still enforced server-side, same as any other client</text>
    <text x="{W-86}" y="916" text-anchor="end" fill="{AMBER_MD}" opacity=".85">polarpoint-io/agenthive-mcp</text>
  </g>
</svg>'''

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(svg)
print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
