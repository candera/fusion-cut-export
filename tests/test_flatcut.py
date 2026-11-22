"""Offline tests for flatcut. Run: python3 -m unittest discover -s tests
The ezdxf read-back tests are skipped unless ezdxf is installed."""
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CutExport"))
import flatcut as fc  # noqa: E402

try:
    import ezdxf
except ImportError:
    ezdxf = None


def rounded_plate(w=100.0, h=60.0, r=10.0, hole=5.0, name="plate"):
    """A plate with filleted corners (arcs), a round hole, and a stroked slot."""
    outer = [
        fc.Line(r, 0, w - r, 0), fc.Arc(w - r, r, r, 270, 0),
        fc.Line(w, r, w, h - r), fc.Arc(w - r, h - r, r, 0, 90),
        fc.Line(w - r, h, r, h), fc.Arc(r, h - r, r, 90, 180),
        fc.Line(0, h - r, 0, r), fc.Arc(r, r, r, 180, 270),
    ]
    slot = [(40, 25), (50, 25), (50, 35), (40, 35), (40, 25)]
    return fc.Part(name, [fc.Loop(True, outer), fc.Loop(False, [fc.Circle(25, 30, hole)]),
                          fc.Loop(False, [fc.Polyline(slot)])])


class Geometry(unittest.TestCase):
    def test_arc_bounds_include_extremes(self):
        arc = fc.Arc(0, 0, 10, 270, 90)  # right half circle
        self.assertAlmostEqual(arc.bounds()[2], 10)
        self.assertAlmostEqual(arc.bounds()[0], 0, places=6)
        self.assertAlmostEqual(arc.bounds()[1], -10)

    def test_rounded_plate_bounds_and_closure(self):
        part = rounded_plate()
        x0, y0, x1, y1 = part.bounds()
        for got, want in zip((x0, y0, x1, y1), (0, 0, 100, 60)):
            self.assertAlmostEqual(got, want, places=6)
        self.assertEqual(fc.loop_problems(part.loops[0]), 0)

    def test_open_contour_is_reported(self):
        loop = fc.Loop(True, [fc.Line(0, 0, 10, 0), fc.Line(10, 0, 10, 10), fc.Line(10, 10, 0, 5)])
        self.assertEqual(fc.loop_problems(loop), 2)

    def test_align_makes_longest_edge_horizontal_and_preserves_size(self):
        part = rounded_plate().moved(fc.rotation(37.0, 5, 9))
        aligned = fc.align_to_longest_edge(part)
        w, h = aligned.size()
        self.assertAlmostEqual(w, 100, places=5)
        self.assertAlmostEqual(h, 60, places=5)

    def test_align_does_not_flip_part_when_edge_is_stored_backwards(self):
        part = rounded_plate()
        part.loops[0].entities[0] = fc.Line(90, 0, 10, 0)  # same edge, opposite direction
        aligned = fc.align_to_longest_edge(part)
        self.assertEqual(aligned.bounds(), part.bounds())
        self.assertAlmostEqual(aligned.loops[1].entities[0].cx, 25)  # hole stays left of center

    def test_layout_wraps_and_spaces(self):
        parts = [rounded_plate(name=f"p{i}") for i in range(3)]
        placed, wide = fc.place_on_sheet(parts, 230.0, 5.0)
        self.assertEqual(wide, [])
        boxes = [p.bounds() for p in placed]
        self.assertAlmostEqual(boxes[1][0] - boxes[0][2], 5.0)       # same row, 5 mm apart
        self.assertAlmostEqual(boxes[2][0], 0.0)                    # third wraps
        self.assertAlmostEqual(boxes[2][1] - boxes[0][3], 5.0)      # next row, 5 mm above

    def test_part_wider_than_sheet_is_reported_not_dropped(self):
        placed, wide = fc.place_on_sheet([rounded_plate(name="big")], 50.0, 5.0)
        self.assertEqual(wide, ["big"])
        self.assertEqual(len(placed), 1)


@unittest.skipIf(ezdxf is None, "ezdxf not installed")
class DxfReadBack(unittest.TestCase):
    def read(self, parts):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "t.dxf"
            path.write_text(fc.dxf_text(parts), encoding="ascii", newline="\r\n")
            doc = ezdxf.readfile(path)
            auditor = doc.audit()
            self.assertEqual(len(auditor.errors), 0, auditor.errors)
            return doc

    def test_entities_layers_and_units(self):
        placed, _ = fc.place_on_sheet([rounded_plate(), rounded_plate(name="b")], 500, 5)
        doc = self.read(placed)
        self.assertEqual(doc.dxfversion, "AC1009")
        self.assertEqual(doc.header["$INSUNITS"], 4)
        msp = doc.modelspace()
        kinds = {}
        for e in msp:
            kinds[e.dxftype()] = kinds.get(e.dxftype(), 0) + 1
        self.assertEqual(kinds, {"LINE": 8, "ARC": 8, "CIRCLE": 2, "POLYLINE": 2})
        self.assertEqual({e.dxf.layer for e in msp if e.dxftype() == "CIRCLE"}, {fc.INNER_LAYER})
        self.assertEqual({e.dxf.layer for e in msp if e.dxftype() == "LINE"}, {fc.OUTER_LAYER})

    def test_geometry_survives_round_trip(self):
        from ezdxf import bbox
        placed, _ = fc.place_on_sheet([rounded_plate()], 500, 5)
        doc = self.read(placed)
        box = bbox.extents(doc.modelspace())
        self.assertAlmostEqual(box.extmin.x, 0, places=4)
        self.assertAlmostEqual(box.extmax.x, 100, places=4)
        self.assertAlmostEqual(box.extmax.y, 60, places=4)
        arcs = [e for e in doc.modelspace() if e.dxftype() == "ARC"]
        # Corner arcs must keep CCW start/end angles exactly.
        self.assertEqual(sorted((round(a.dxf.start_angle), round(a.dxf.end_angle)) for a in arcs),
                         [(0, 90), (90, 180), (180, 270), (270, 0)])


if __name__ == "__main__":
    unittest.main()
