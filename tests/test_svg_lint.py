import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINT = ROOT / "template" / "claude" / "tools" / "svg_lint.py"
TEMPLATE_SVG = ROOT / "template" / "claude" / "skills" / "diagram-maker" / "references" / "svg-architecture-light.svg"
TEMPLATE_MMD = ROOT / "template" / "claude" / "skills" / "diagram-maker" / "references" / "mermaid-templates.md"

GOOD_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 104" role="img" aria-labelledby="t d">
  <title id="t">Example</title><desc id="d">Two boxes</desc>
  <style>:root{--fg:#1f2937} @media (prefers-color-scheme: dark){:root{--fg:#e5e7eb}} text{font-size:14px;fill:var(--fg)}</style>
  <rect x="8" y="8" width="80" height="40" fill="none" stroke="currentColor"/>
  <text x="16" y="32">A</text>
</svg>"""

GOOD_MD = """# Doc

```mermaid
%%{init: {'theme':'base','flowchart':{'curve':'linear','nodeSpacing':48,'rankSpacing':64,'padding':16}}}%%
flowchart LR
  accTitle: Request path
  accDescr: The client calls the API, which reads the database.
  A[Client]:::svc --> B[API]:::svc
  B --> C[(DB)]:::data
  classDef svc fill:#eef,stroke:#336
  classDef data fill:#efe,stroke:#363
```
"""


def run(*files):
    p = subprocess.run([sys.executable, str(LINT), *map(str, files)], capture_output=True, text=True)
    return p.returncode, p.stdout


class SvgLintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_good_svg_passes(self):
        code, out = run(self.write("good.svg", GOOD_SVG))
        self.assertEqual(code, 0, out)
        self.assertNotIn("ERROR", out)

    def test_malformed_svg(self):
        code, out = run(self.write("bad.svg", "<svg><g></svg>"))
        self.assertEqual(code, 1)
        self.assertIn("not well-formed", out)

    def test_missing_viewbox_and_title(self):
        code, out = run(self.write("bad.svg", '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'))
        self.assertEqual(code, 1)
        self.assertIn("viewBox", out)
        self.assertIn("<title>", out)

    def test_script_handlers_external_refs(self):
        svg = GOOD_SVG.replace(
            "<rect ",
            '<script>alert(1)</script><image href="https://evil.example/x.png"/><rect onclick="x()" ',
        )
        code, out = run(self.write("bad.svg", svg))
        self.assertEqual(code, 1)
        self.assertIn("<script>", out)
        self.assertIn("event handler", out)
        self.assertIn("external reference", out)

    def test_foreign_object(self):
        svg = GOOD_SVG.replace("<rect ", "<foreignObject/><rect ")
        code, out = run(self.write("bad.svg", svg))
        self.assertEqual(code, 1)
        self.assertIn("foreignObject", out)

    def test_small_font(self):
        svg = GOOD_SVG.replace('<text x="16"', '<text font-size="8" x="16"')
        code, out = run(self.write("bad.svg", svg))
        self.assertEqual(code, 1)
        self.assertIn("font-size 8", out)

    def test_hardcoded_colors_warn_only(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" role="img">'
               '<title>x</title><desc>y</desc><rect fill="#ff0000"/></svg>')
        code, out = run(self.write("c.svg", svg))
        self.assertEqual(code, 0, out)
        self.assertIn("WARN: hardcoded hex colors", out)

    def test_good_markdown(self):
        code, out = run(self.write("d.md", GOOD_MD))
        self.assertEqual(code, 0, out)
        self.assertNotIn("WARN", out)

    def test_empty_mermaid_block(self):
        code, out = run(self.write("d.md", "```mermaid\n\n```\n"))
        self.assertEqual(code, 1)
        self.assertIn("empty", out)

    def test_unbalanced_brackets(self):
        code, out = run(self.write("d.mmd", "%%{init: {}}%%\nflowchart LR\n  A[Client --> B[API]\n"))
        self.assertEqual(code, 1)
        self.assertIn("unbalanced", out)

    def test_too_many_nodes_and_no_palette_warn(self):
        body = "flowchart LR\n" + "\n".join(f"  N{i}[Node {i}] --> N{i+1}[Node {i+1}]" for i in range(14))
        code, out = run(self.write("big.mmd", body))
        self.assertEqual(code, 0, out)
        self.assertIn("nodes (> 12)", out)
        self.assertIn("palette", out)

    def test_quoted_label_brackets_ok(self):
        code, out = run(self.write("q.mmd", '%%{init: {}}%%\nflowchart LR\n  A["List [x] (y)"] --> B\n'))
        self.assertEqual(code, 0, out)

    def test_unsupported_type(self):
        code, out = run(self.write("x.txt", "hi"))
        self.assertEqual(code, 1)
        self.assertIn("unsupported", out)

    def test_shipped_templates_are_clean(self):
        ref = os.path.join(os.path.dirname(str(LINT)), "..", "skills", "diagram-maker", "references")
        files = sorted(os.path.join(ref, f) for f in os.listdir(ref) if f.endswith((".svg", ".md")))
        self.assertEqual(len([f for f in files if f.endswith(".svg")]), 6)
        files.append(str(ROOT / "docs" / "diagrams" / "tool-call-flow.svg"))
        r = subprocess.run([sys.executable, str(LINT), *files], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "svg_lint: OK", r.stdout)

    def test_generated_templates_match_generator(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import gen_diagram_templates as g
        ref = Path(g.REF)
        for kind in g.KINDS:
            for theme in g.THEMES:
                with self.subTest(kind=kind, theme=theme):
                    self.assertEqual((ref / f"svg-{kind}-{theme}.svg").read_text(), g.render(kind, theme),
                                     "edit scripts/gen_diagram_templates.py and rerun it; don't edit generated files")
        self.assertNotIn("prefers-color-scheme", (ref / "svg-flow-light.svg").read_text())
        self.assertIn("prefers-color-scheme", (ref / "svg-flow-auto.svg").read_text())

    def test_mermaid_precision(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "m.md")
        with open(p, "w") as fh:
            fh.write("```mermaid\nflowchart\n  a[Start here]:::svc --> b[Process The Order]\n  style b fill:#f00\n"
                     "  classDef svc fill:#DBEAFE\n```\n")
        r = subprocess.run([sys.executable, str(LINT), p], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        for needle in ("no direction", "inline `style`", "accTitle", "mix Title Case", "without a palette class"):
            self.assertIn(needle, r.stdout)


class TestPrecision(unittest.TestCase):
    """The precision standard: grid, alignment, spacing, consistency, clean edges."""

    HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 160" role="img"><title>t</title><desc>d</desc>'
            '<style>text { font-family: sans-serif; font-size: 14px; } .edge { stroke-width: 1.5px; }</style>')

    def lint(self, body):
        p = os.path.join(tempfile.mkdtemp(), "x.svg")
        with open(p, "w") as fh:
            fh.write(self.HEAD + body + "</svg>")
        r = subprocess.run([sys.executable, str(LINT), p], capture_output=True, text=True)
        return r.returncode, r.stdout

    def test_clean_layout_passes(self):
        code, out = self.lint(
            '<rect x="24" y="48" width="128" height="64" rx="8"/><text x="88" y="80" text-anchor="middle">A</text>'
            '<rect x="200" y="48" width="128" height="64" rx="8"/><text x="264" y="80" text-anchor="middle">B</text>'
            '<path class="edge" d="M152 80 H200" marker-end="url(#a)"/>')
        self.assertEqual(code, 0, out)
        self.assertNotIn("WARN", out)

    def test_off_grid_and_near_miss(self):
        code, out = self.lint('<rect x="24" y="48" width="128" height="64"/><rect x="200" y="51" width="128" height="64"/>')
        self.assertEqual(code, 1)
        self.assertIn("off the 8px grid", out)
        self.assertIn("near-miss alignment", out)

    def test_uneven_spacing_and_heights(self):
        code, out = self.lint('<rect x="24" y="48" width="64" height="48"/><rect x="112" y="48" width="64" height="48"/>'
                              '<rect x="216" y="40" width="64" height="64"/>')
        self.assertEqual(code, 1)
        self.assertIn("uneven horizontal spacing", out)
        self.assertIn("different heights", out)

    def test_uneven_spacing_counts_diamonds(self):
        code, out = self.lint('<rect x="24" y="16" width="64" height="32"/>'
                              '<polygon points="56,64 88,80 56,96 24,80"/>'
                              '<rect x="24" y="128" width="64" height="32"/>')
        self.assertEqual(code, 1)
        self.assertIn("uneven vertical spacing", out)

    def test_off_center_label_and_crossing_edges(self):
        code, out = self.lint(
            '<rect x="24" y="48" width="128" height="64"/><text x="80" y="80" text-anchor="middle">A</text>'
            '<path class="edge" d="M200 16 V144"/><path class="edge" d="M160 128 H320"/>')
        self.assertEqual(code, 1)
        self.assertIn("not centered", out)
        self.assertIn("edges cross", out)

    def test_edge_through_box_and_style_sprawl(self):
        code, out = self.lint(
            '<rect x="160" y="48" width="64" height="64"/><path class="edge" d="M24 80 H400"/>'
            '<rect x="24" y="136" width="16" height="16" rx="2" stroke-width="1"/><rect x="48" y="136" width="16" height="16" rx="4" stroke-width="2"/>'
            '<rect x="72" y="136" width="16" height="16" rx="6" stroke-width="3"/>')
        self.assertEqual(code, 1)
        self.assertIn("passes through the box", out)
        self.assertIn("different stroke widths", out)
        self.assertIn("different corner radii", out)

    def test_defs_styles_do_not_count(self):
        code, out = self.lint('<defs><pattern id="g" width="16" height="16"><path d="M16 0 H0 V16" stroke-width="0.5"/></pattern></defs>'
                              '<rect x="24" y="48" width="128" height="64" stroke-width="3"/>')
        self.assertNotIn("different stroke widths", out)
