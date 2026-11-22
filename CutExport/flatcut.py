"""Flat 2D geometry, sheet layout, and ASCII DXF output for the cut-export add-in.

Nothing here imports Fusion, so it can be tested with plain Python. All lengths
are millimetres and all angles are degrees unless a name says otherwise.
"""

from dataclasses import dataclass, field
import math

OUTER_LAYER = "CUT_OUTER"
INNER_LAYER = "CUT_INNER"
JOIN_TOLERANCE = 0.002  # mm; endpoints closer than this count as joined.


@dataclass
class Line:
    x0: float
    y0: float
    x1: float
    y1: float

    def ends(self):
        return (self.x0, self.y0), (self.x1, self.y1)

    def bounds(self):
        return min(self.x0, self.x1), min(self.y0, self.y1), max(self.x0, self.x1), max(self.y0, self.y1)

    def length(self):
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)

    def moved(self, move):
        (a, b), (c, d) = move(self.x0, self.y0), move(self.x1, self.y1)
        return Line(a, b, c, d)


@dataclass
class Arc:
    """Counterclockwise arc from start to end, as DXF defines it."""
    cx: float
    cy: float
    r: float
    start: float
    end: float

    def sweep(self):
        return (self.end - self.start) % 360.0 or 360.0

    def point(self, angle):
        a = math.radians(angle)
        return self.cx + self.r * math.cos(a), self.cy + self.r * math.sin(a)

    def ends(self):
        return self.point(self.start), self.point(self.end)

    def bounds(self):
        points = [self.point(self.start), self.point(self.end)]
        sweep = self.sweep()
        for quadrant in (0.0, 90.0, 180.0, 270.0):
            if (quadrant - self.start) % 360.0 <= sweep:
                points.append(self.point(quadrant))
        xs, ys = zip(*points)
        return min(xs), min(ys), max(xs), max(ys)

    def moved(self, move):
        # A move is a rotation plus translation, so angles shift by the rotation.
        cx, cy = move(self.cx, self.cy)
        ox, oy = move(0.0, 0.0)
        rx, ry = move(1.0, 0.0)
        turn = math.degrees(math.atan2(ry - oy, rx - ox))
        return Arc(cx, cy, self.r, (self.start + turn) % 360.0, (self.end + turn) % 360.0)


@dataclass
class Circle:
    cx: float
    cy: float
    r: float

    def ends(self):
        return None

    def bounds(self):
        return self.cx - self.r, self.cy - self.r, self.cx + self.r, self.cy + self.r

    def moved(self, move):
        return Circle(*move(self.cx, self.cy), self.r)


@dataclass
class Polyline:
    """An open chain of straight segments, used for splines and ellipses."""
    points: list

    def ends(self):
        return self.points[0], self.points[-1]

    def bounds(self):
        xs, ys = zip(*self.points)
        return min(xs), min(ys), max(xs), max(ys)

    def moved(self, move):
        return Polyline([move(x, y) for x, y in self.points])


@dataclass
class Loop:
    outer: bool
    entities: list

    def bounds(self):
        return merge_bounds(entity.bounds() for entity in self.entities)


@dataclass
class Part:
    name: str
    loops: list
    warnings: list = field(default_factory=list)

    def bounds(self):
        return merge_bounds(loop.bounds() for loop in self.loops)

    def size(self):
        x0, y0, x1, y1 = self.bounds()
        return x1 - x0, y1 - y0

    def moved(self, move):
        return Part(self.name, [Loop(loop.outer, [e.moved(move) for e in loop.entities]) for loop in self.loops],
                    list(self.warnings))


def merge_bounds(boxes):
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def rotation(degrees, dx=0.0, dy=0.0):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return lambda x, y: (c * x - s * y + dx, s * x + c * y + dy)


def loop_problems(loop):
    """Describe endpoints of a loop that do not meet another entity."""
    ends = []
    for index, entity in enumerate(loop.entities):
        pair = entity.ends()
        if pair is not None:
            ends.extend((index, point) for point in pair)
    problems = 0
    for index, (owner, point) in enumerate(ends):
        if not any(other != index and math.dist(point, p) <= JOIN_TOLERANCE for other, (_, p) in enumerate(ends)):
            problems += 1
    return problems


def align_to_longest_edge(part):
    """Rotate so the longest straight edge on the outer loop is horizontal."""
    lines = [e for loop in part.loops if loop.outer for e in loop.entities if isinstance(e, Line)]
    if not lines:
        return part
    longest = max(lines, key=Line.length)
    angle = math.degrees(math.atan2(longest.y1 - longest.y0, longest.x1 - longest.x0))
    angle = (angle + 90.0) % 180.0 - 90.0  # An edge is undirected: turn the part by at most 90 degrees.
    return part.moved(rotation(-angle))


def place_on_sheet(parts, sheet_width, spacing):
    """Pack parts left to right in rows, in the order given, with the lower left at the origin.

    A part wider than the sheet still gets its own row and is reported back.
    """
    placed, too_wide = [], []
    x = y = row_height = 0.0
    for part in parts:
        x0, y0, x1, y1 = part.bounds()
        width, height = x1 - x0, y1 - y0
        if x > 0 and x + width > sheet_width:
            x, y, row_height = 0.0, y + row_height + spacing, 0.0
        if width > sheet_width:
            too_wide.append(part.name)
        placed.append(part.moved(lambda px, py, dx=x - x0, dy=y - y0: (px + dx, py + dy)))
        x += width + spacing
        row_height = max(row_height, height)
    return placed, too_wide


def entity_counts(parts):
    counts = {}
    for part in parts:
        for loop in part.loops:
            for entity in loop.entities:
                counts[type(entity).__name__] = counts.get(type(entity).__name__, 0) + 1
    return counts


def number(value):
    text = f"{value:.5f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def dxf_text(parts):
    """ASCII DXF release 12 text for already-placed parts.

    R12 uses only LINE, ARC, CIRCLE, and POLYLINE entities, which every laser
    and plasma package reads. $INSUNITS tells newer readers the unit is mm.
    """
    out = []

    def pair(code, value):
        out.append(f"{code:>3}\n{value}\n")

    def point(x, y, offset=0):
        pair(10 + offset, number(x))
        pair(20 + offset, number(y))
        pair(30 + offset, "0")

    x0, y0, x1, y1 = merge_bounds(part.bounds() for part in parts)
    for header in (
        (0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1009"),
        (9, "$INSUNITS"), (70, 4), (9, "$MEASUREMENT"), (70, 1),
    ):
        pair(*header)
    pair(9, "$EXTMIN"); point(x0, y0)
    pair(9, "$EXTMAX"); point(x1, y1)
    for item in ((0, "ENDSEC"), (0, "SECTION"), (2, "TABLES"), (0, "TABLE"), (2, "LTYPE"), (70, 1),
                 (0, "LTYPE"), (2, "CONTINUOUS"), (70, 0), (3, "Solid line"), (72, 65), (73, 0), (40, "0.0"),
                 (0, "ENDTAB"), (0, "TABLE"), (2, "LAYER"), (70, 2)):
        pair(*item)
    for name, color in ((INNER_LAYER, 5), (OUTER_LAYER, 1)):
        for item in ((0, "LAYER"), (2, name), (70, 0), (62, color), (6, "CONTINUOUS")):
            pair(*item)
    for item in ((0, "ENDTAB"), (0, "ENDSEC"), (0, "SECTION"), (2, "ENTITIES")):
        pair(*item)

    for part in parts:
        for loop in part.loops:
            layer = OUTER_LAYER if loop.outer else INNER_LAYER
            for entity in loop.entities:
                pair(0, {Line: "LINE", Arc: "ARC", Circle: "CIRCLE", Polyline: "POLYLINE"}[type(entity)])
                pair(8, layer)
                if isinstance(entity, Line):
                    point(entity.x0, entity.y0)
                    point(entity.x1, entity.y1, 1)
                elif isinstance(entity, Arc):
                    point(entity.cx, entity.cy)
                    pair(40, number(entity.r))
                    pair(50, number(entity.start))
                    pair(51, number(entity.end))
                elif isinstance(entity, Circle):
                    point(entity.cx, entity.cy)
                    pair(40, number(entity.r))
                else:
                    pair(66, 1)
                    pair(70, 0)
                    point(0, 0)
                    for x, y in entity.points:
                        pair(0, "VERTEX")
                        pair(8, layer)
                        point(x, y)
                    pair(0, "SEQEND")
                    pair(8, layer)
    pair(0, "ENDSEC")
    pair(0, "EOF")
    return "".join(out)
