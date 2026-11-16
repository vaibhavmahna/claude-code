#!/usr/bin/env python3
"""Generate the diagram-maker SVG templates: {architecture, flow} x {light, dark, auto} from one source.

Usage: python3 scripts/gen_diagram_templates.py          # rewrites template/.../references/svg-*.svg
Edit the layouts or palettes here, never the generated files; tests fail if they drift.
"""
import os
import sys

REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "template", "claude",
                   "skills", "diagram-maker", "references")

LIGHT = """      --bg: #FAFAFA;
      --fg: #0F172A;
      --muted: #475569;
      --edge: #64748B;
      --err: #DC2626;
      --svc-fill: #DBEAFE;  --svc-stroke: #2563EB;
      --data-fill: #DCFCE7; --data-stroke: #16A34A;
      --ext-fill: #F1F5F9;  --ext-stroke: #64748B;
      --user-fill: #FEF3C7; --user-stroke: #D97706;
      --err-fill: #FEE2E2;  --err-stroke: #DC2626;
      --grid: #E5E5E5;"""
DARK = """      --bg: #0B1220;
      --fg: #E2E8F0;
      --muted: #94A3B8;
      --edge: #94A3B8;
      --err: #F87171;
      --svc-fill: #1E3A8A;  --svc-stroke: #60A5FA;
      --data-fill: #14532D; --data-stroke: #4ADE80;
      --ext-fill: #1E293B;  --ext-stroke: #94A3B8;
      --user-fill: #78350F; --user-stroke: #FBBF24;
      --err-fill: #450A0A;  --err-stroke: #F87171;
      --grid: #1E293B;"""

def root_block(theme):
    if theme == "light":
        return f"    /* theme: light (fixed) */\n    :root {{\n{LIGHT}\n    }}"
    if theme == "dark":
        return f"    /* theme: dark (fixed) */\n    :root {{\n{DARK}\n    }}"
    dark = "\n".join("  " + l for l in DARK.splitlines())
    return (f"    /* theme: auto (follows the viewer's light/dark setting) */\n    :root {{\n{LIGHT}\n    }}\n"
            f"    @media (prefers-color-scheme: dark) {{\n      :root {{\n{dark}\n      }}\n    }}")

CLASSES = """    svg { color: var(--edge); }
    text { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; dominant-baseline: middle; }
    .bg { fill: var(--bg); }
    .title { font-size: 16px; font-weight: 700; fill: var(--fg); }
    .label { font-size: 14px; font-weight: 600; fill: var(--fg); }
    .sub { font-size: 12px; fill: var(--muted); }
    .sub-err { font-size: 12px; fill: var(--err); }
    .box { stroke-width: 1.5px; }
    .user { fill: var(--user-fill); stroke: var(--user-stroke); }
    .svc  { fill: var(--svc-fill);  stroke: var(--svc-stroke); }
    .data { fill: var(--data-fill); stroke: var(--data-stroke); }
    .ext  { fill: var(--ext-fill);  stroke: var(--ext-stroke); stroke-dasharray: 4 4; }
    .err  { fill: var(--err-fill);  stroke: var(--err-stroke); }
    .edge { fill: none; stroke: currentColor; stroke-width: 1.5px; }
    .edge-secondary { stroke-dasharray: 4 4; }
    .edge-err { stroke: var(--err); }"""

DEFS = """  <defs>
    <!-- userSpaceOnUse + refX at the tip: the arrow tip lands exactly on the target border -->
    <marker id="arrow" viewBox="0 0 8 8" refX="8" refY="4" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
      <path d="M0 0 L8 4 L0 8 z" fill="currentColor"/>
    </marker>
    <marker id="arrow-err" viewBox="0 0 8 8" refX="8" refY="4" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
      <path d="M0 0 L8 4 L0 8 z" fill="var(--err)"/>
    </marker>
    <!-- Optional blueprint grid: set the second background rect's fill to url(#grid) to use it -->
    <pattern id="grid" width="16" height="16" patternUnits="userSpaceOnUse">
      <path d="M16 0 H0 V16" fill="none" stroke="var(--grid)" stroke-width="0.5"/>
    </pattern>
  </defs>"""

ARCH_HEAD = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 704 288" role="img" aria-labelledby="diagram-title diagram-desc">
  <title id="diagram-title">Request flow: client, API and database</title>
  <desc id="diagram-desc">A client sends HTTPS requests to the API service, which reads from and writes to a Postgres database. The API also charges an external payment provider.</desc>"""

ARCH_BODY = """
  <!--
    Architecture template (left to right). 8px grid, 24px margins, every box 144 x 64.
    Row y=104 with centre line y=136; columns x=24, 280, 536 (equal 112px gaps); second row y=200.
    Labels: title at box centre - 8, caption at centre + 8, both dominant-baseline middle.
  -->
  <rect class="bg" x="0" y="0" width="704" height="288"/>

  <!-- Legend: fixed 128px slots, swatch 16x16, label 24px right of the swatch -->
  <rect class="box user" x="24" y="24" width="16" height="16" rx="4"/>
  <text class="sub" x="48" y="32">actor</text>
  <rect class="box svc" x="152" y="24" width="16" height="16" rx="4"/>
  <text class="sub" x="176" y="32">service</text>
  <rect class="box data" x="280" y="24" width="16" height="16" rx="4"/>
  <text class="sub" x="304" y="32">data store</text>
  <rect class="box ext" x="408" y="24" width="16" height="16" rx="4"/>
  <text class="sub" x="432" y="32">external</text>

  <!-- Client (actor) -->
  <rect class="box user" x="24" y="104" width="144" height="64" rx="8"/>
  <text class="label" x="96" y="128" text-anchor="middle">Client</text>
  <text class="sub" x="96" y="144" text-anchor="middle">browser</text>

  <!-- API (service) -->
  <rect class="box svc" x="280" y="104" width="144" height="64" rx="8"/>
  <text class="label" x="352" y="128" text-anchor="middle">API</text>
  <text class="sub" x="352" y="144" text-anchor="middle">FastAPI</text>

  <!-- Postgres (data store): cylinder in the same 144 x 64 footprint; rim kept flat (ry 4) so the title clears it -->
  <path class="box data" d="M536 108 A72 4 0 0 1 680 108 V164 A72 4 0 0 1 536 164 Z"/>
  <path class="box data" d="M536 108 A72 4 0 0 0 680 108" fill="none"/>
  <text class="label" x="608" y="128" text-anchor="middle">Postgres</text>
  <text class="sub" x="608" y="144" text-anchor="middle">orders</text>

  <!-- Payments (external) -->
  <rect class="box ext" x="280" y="200" width="144" height="64" rx="8"/>
  <text class="label" x="352" y="224" text-anchor="middle">Payments</text>
  <text class="sub" x="352" y="240" text-anchor="middle">external</text>

  <!-- Edges: start and end exactly on borders; labels 16px above the line, centred on the gap -->
  <path class="edge" d="M168 136 H280" marker-end="url(#arrow)"/>
  <text class="sub" x="224" y="120" text-anchor="middle">HTTPS</text>
  <path class="edge" d="M424 136 H536" marker-end="url(#arrow)"/>
  <text class="sub" x="480" y="120" text-anchor="middle">SQL</text>
  <path class="edge" d="M352 168 V200" marker-end="url(#arrow)"/>
  <text class="sub" x="360" y="184">charges</text>
</svg>
"""

FLOW_HEAD = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 528 704" role="img" aria-labelledby="diagram-title diagram-desc">
  <title id="diagram-title">Order validation flow</title>
  <desc id="diagram-desc">A request is validated. Valid requests are processed and saved, then a response is sent. Invalid requests are rejected and a 400 response is sent.</desc>"""

FLOW_BODY = """
  <!--
    Flow template (top to bottom). 8px grid, 24px margins.
    Main column: x=24, width 192, centre x=120; side column: x=312 (96px gap).
    Every step is 192 x 64 (start/end pills 192 x 48, rx 24); the decision diamond is 192 x 96.
    Equal 32px gaps between every element in the main column. Error path in red, on the right.
  -->
  <rect class="bg" x="0" y="0" width="528" height="704"/>

  <!-- Title and legend -->
  <text class="title" x="24" y="32">Order validation flow</text>
  <rect class="box user" x="24" y="56" width="16" height="16" rx="8"/>
  <text class="sub" x="48" y="64">start / end</text>
  <rect class="box svc" x="152" y="56" width="16" height="16" rx="8"/>
  <text class="sub" x="176" y="64">step</text>
  <rect class="box err" x="280" y="56" width="16" height="16" rx="8"/>
  <text class="sub" x="304" y="64">error path</text>

  <!-- Start -->
  <rect class="box user" x="24" y="104" width="192" height="48" rx="24"/>
  <text class="label" x="120" y="128" text-anchor="middle">Request received</text>

  <!-- Validate -->
  <rect class="box svc" x="24" y="184" width="192" height="64" rx="8"/>
  <text class="label" x="120" y="208" text-anchor="middle">Validate input</text>
  <text class="sub" x="120" y="224" text-anchor="middle">schema check</text>

  <!-- Decision -->
  <polygon class="box svc" points="120,280 216,328 120,376 24,328"/>
  <text class="label" x="120" y="328" text-anchor="middle">Valid?</text>

  <!-- Process -->
  <rect class="box svc" x="24" y="408" width="192" height="64" rx="8"/>
  <text class="label" x="120" y="432" text-anchor="middle">Process order</text>
  <text class="sub" x="120" y="448" text-anchor="middle">pricing, stock</text>

  <!-- Save -->
  <rect class="box svc" x="24" y="504" width="192" height="64" rx="8"/>
  <text class="label" x="120" y="528" text-anchor="middle">Save order</text>
  <text class="sub" x="120" y="544" text-anchor="middle">orders table</text>

  <!-- End -->
  <rect class="box user" x="24" y="600" width="192" height="48" rx="24"/>
  <text class="label" x="120" y="624" text-anchor="middle">Response sent</text>

  <!-- Reject (error path) -->
  <rect class="box err" x="312" y="296" width="192" height="64" rx="8"/>
  <text class="label" x="408" y="320" text-anchor="middle">Reject request</text>
  <text class="sub" x="408" y="336" text-anchor="middle">collect errors</text>

  <!-- Edges: solid = main flow, red = error path; labels 8px right of vertical runs, 16px above horizontal runs -->
  <path class="edge" d="M120 152 V184" marker-end="url(#arrow)"/>
  <path class="edge" d="M120 248 V280" marker-end="url(#arrow)"/>
  <path class="edge" d="M120 376 V408" marker-end="url(#arrow)"/>
  <text class="sub" x="128" y="392">yes</text>
  <path class="edge" d="M120 472 V504" marker-end="url(#arrow)"/>
  <path class="edge" d="M120 568 V600" marker-end="url(#arrow)"/>
  <path class="edge edge-err" d="M216 328 H312" marker-end="url(#arrow-err)"/>
  <text class="sub-err" x="264" y="312" text-anchor="middle">no</text>
  <path class="edge edge-err" d="M408 360 V624 H216" marker-end="url(#arrow-err)"/>
  <text class="sub-err" x="416" y="496">400 response</text>

  <!-- Caption: one sentence that states what the diagram shows -->
  <text class="sub" x="24" y="680">Invalid requests never reach the order table.</text>
</svg>
"""

THEMES = ("light", "dark", "auto")
KINDS = {"architecture": (ARCH_HEAD, ARCH_BODY), "flow": (FLOW_HEAD, FLOW_BODY)}


def style_and_defs(theme):
    """The <style> and <defs> blocks for a theme (also used to restyle existing diagrams)."""
    return f"  <style>\n{root_block(theme)}\n{CLASSES}\n  </style>\n{DEFS}"


def render(kind, theme):
    head, body = KINDS[kind]
    return f"{head}\n{style_and_defs(theme)}\n{body}"


def main():
    for kind in KINDS:
        for theme in THEMES:
            with open(os.path.join(REF, f"svg-{kind}-{theme}.svg"), "w") as fh:
                fh.write(render(kind, theme))
    print(f"wrote {len(KINDS) * len(THEMES)} templates to {os.path.normpath(REF)}")


if __name__ == "__main__":
    sys.exit(main())
