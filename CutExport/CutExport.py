"""Export several planar faces from Fusion to one DXF for laser or plasma cutting."""

from dataclasses import dataclass
from json import loads
import math
from pathlib import Path
import sys
import traceback

import adsk.core as core
import adsk.fusion as fusion

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flatcut  # noqa: E402  (sibling module; needs the path entry above)

COMMAND_ID = "Making_CutExport"
COMMAND_NAME = "Export Faces for Cutting"
ICON_FOLDER = Path(__file__).resolve().parent / "Resources"
VERSION = loads(Path(__file__).with_suffix(".manifest").read_text(encoding="utf-8"))["version"]
CM_TO_MM = 10.0
PARALLEL_TOLERANCE = 1e-5  # Allowed 1 - |cos| between face normals sharing a frame.
STROKE_TOLERANCE_MM = 0.005  # Maximum chord error when a spline or ellipse becomes line segments.
LAYOUTS = {"keep": "Keep arrangement", "rows": "Lay out in rows"}
ORIENTATIONS = {"edge": "Align to longest edge", "view": "Keep face orientation"}
handlers = []
last_folder = []


class ExportError(Exception):
    pass


@dataclass
class Options:
    align: bool         # Rows layout only: turn each part so its longest edge is horizontal.
    spacing: float      # mm, rows layout only
    sheet_width: float  # mm, rows layout only
    keep: bool = True   # Keep the faces where they are relative to each other.


def require(result, message):
    if not result:
        raise ExportError(message)
    return result


def flattener(origin, u, v):
    """Map a world point (cm) to coordinates (mm) along u and v from origin."""
    def flatten(point):
        d = origin.vectorTo(point)
        return d.dotProduct(u) * CM_TO_MM, d.dotProduct(v) * CM_TO_MM
    return flatten


def outward_normal(face):
    ok, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
    require(ok, "Could not read a face normal.")
    normal.normalize()
    return normal


def face_frame(face):
    """A frame for one face: its outside view, x along the plane's own u direction.

    Returns (flatten, normal). y completes a right-handed frame with the outward normal.
    """
    plane = require(core.Plane.cast(face.geometry), "Only planar faces can be exported.")
    normal = outward_normal(face)
    u = plane.uDirection
    u.subtract(_scaled(normal, u.dotProduct(normal)))
    u.normalize()
    return flattener(plane.origin, u, normal.crossProduct(u)), normal


def shared_frame(faces, names):
    """One frame for every face, so their positions relative to each other are kept.

    The view looks at the faces from the side their normals share, preferring +Z so that a
    flat arrangement in the XY plane (such as Arrange makes) reads as seen from above. x and y
    follow Fusion's standard views. A face on the far side of its part is drawn as seen
    through the part, which is where its outline sits in the arrangement.
    """
    normals = [outward_normal(face) for face in faces]
    view = normals[0].copy()
    if abs(view.z) > 0.5 and view.z < 0:
        view.scaleBy(-1)
    for name, normal in zip(names, normals):
        if abs(normal.dotProduct(view)) < 1 - PARALLEL_TOLERANCE:
            raise ExportError(f"{name} is not parallel to the first face, so the arrangement cannot be kept. "
                              "Select faces that lie flat in the same direction, or choose the row layout.")
    up = core.Vector3D.create(0, 0, 1) if abs(view.z) < 1 - PARALLEL_TOLERANCE else core.Vector3D.create(0, 1, 0)
    u = up.crossProduct(view)
    u.normalize()
    return flattener(faces[0].pointOnFace, u, view.crossProduct(u)), view


def _scaled(vector, factor):
    result = vector.copy()
    result.scaleBy(factor)
    return result


def edge_entity(edge, flatten, normal):
    geometry = edge.geometry
    kind = geometry.objectType
    if kind == core.Line3D.classType():
        (x0, y0), (x1, y1) = flatten(geometry.startPoint), flatten(geometry.endPoint)
        return flatcut.Line(x0, y0, x1, y1)
    if kind == core.Circle3D.classType():
        cx, cy = flatten(geometry.center)
        return flatcut.Circle(cx, cy, geometry.radius * CM_TO_MM)
    if kind == core.Arc3D.classType():
        center, start, end = flatten(geometry.center), flatten(geometry.startPoint), flatten(geometry.endPoint)
        if math.dist(start, end) < 1e-6:
            return flatcut.Circle(*center, geometry.radius * CM_TO_MM)
        if geometry.normal.dotProduct(normal) < 0:  # Runs clockwise in the outside view.
            start, end = end, start
        return flatcut.Arc(*center, geometry.radius * CM_TO_MM,
                           math.degrees(math.atan2(start[1] - center[1], start[0] - center[0])) % 360.0,
                           math.degrees(math.atan2(end[1] - center[1], end[0] - center[0])) % 360.0)
    evaluator = edge.evaluator
    ok, low, high = evaluator.getParameterExtents()
    require(ok, "Could not read a curve's extent.")
    ok, points = evaluator.getStrokes(low, high, STROKE_TOLERANCE_MM / CM_TO_MM)
    require(ok and len(points) >= 2, "Could not approximate a curve.")
    return flatcut.Polyline([flatten(point) for point in points])


def face_part(face, name, frame=None):
    flatten, normal = frame or face_frame(face)
    loops = [flatcut.Loop(loop.isOuter, [edge_entity(edge, flatten, normal) for edge in loop.edges])
             for loop in face.loops]
    part = flatcut.Part(name, loops)
    open_ends = sum(flatcut.loop_problems(loop) for loop in loops)
    if open_ends:
        part.warnings.append(f"{name}: {open_ends} edge ends do not meet; the contour may be open.")
    return part


def face_label(face, taken):
    body = face.body
    owner = body.assemblyContext.name if body.assemblyContext else body.name  # Panels often share a body name.
    base = f"{owner} face"
    index = 1
    while f"{base} {index}" in taken:
        index += 1
    return f"{base} {index}"


def build_parts(faces, options):
    unique, names = [], []
    for face in faces:
        if face.entityToken not in {f.entityToken for f in unique}:
            unique.append(face)
            names.append(face_label(face, set(names)))
    require(unique, "Select at least one planar face.")
    if options.keep:
        frame = shared_frame(unique, names)
        parts = [face_part(face, name, frame) for face, name in zip(unique, names)]
        return flatcut.to_origin(parts), []
    parts = [face_part(face, name) for face, name in zip(unique, names)]
    if options.align:
        parts = [flatcut.align_to_longest_edge(part) for part in parts]
    return flatcut.place_on_sheet(parts, options.sheet_width, options.spacing)


def describe(parts, too_wide):
    x0, y0, x1, y1 = flatcut.merge_bounds(part.bounds() for part in parts)
    lines = [f"{len(parts)} part{'s' if len(parts) != 1 else ''}, {x1 - x0:.1f} × {y1 - y0:.1f} mm in all."]
    lines += [f"{p.name}: {p.size()[0]:.1f} × {p.size()[1]:.1f} mm, {len(p.loops) - 1} hole(s)." for p in parts]
    lines += [warning for part in parts for warning in part.warnings]
    lines += [f"{name} is wider than the sheet width." for name in too_wide]
    return "\n".join(lines)


def export_faces(faces, path, options):
    """Write the DXF and return a description of what it holds."""
    parts, too_wide = build_parts(faces, options)
    Path(path).write_text(flatcut.dxf_text(parts), encoding="ascii", newline="\r\n")
    counts = ", ".join(f"{n} {kind}" for kind, n in sorted(flatcut.entity_counts(parts).items()))
    return f"{describe(parts, too_wide)}\nEntities: {counts}."


def active_design():
    return require(fusion.Design.cast(core.Application.get().activeProduct), "Open a Fusion design first.")


def selected_faces(inputs):
    selection = inputs.itemById("faces")
    return [fusion.BRepFace.cast(selection.selection(i).entity) for i in range(selection.selectionCount)]


def settings(inputs):
    return Options(inputs.itemById("orientation").selectedItem.name == ORIENTATIONS["edge"],
                   inputs.itemById("spacing").value * CM_TO_MM,
                   inputs.itemById("sheet").value * CM_TO_MM,
                   inputs.itemById("layout").selectedItem.name == LAYOUTS["keep"])


def show_layout_inputs(inputs):
    rows = inputs.itemById("layout").selectedItem.name == LAYOUTS["rows"]
    for key in ("orientation", "spacing", "sheet"):
        inputs.itemById(key).isVisible = rows


class InputsChanged(core.InputChangedEventHandler):
    def notify(self, args):
        if args.input.id not in ("faces", "layout", "orientation", "spacing", "sheet"):
            return
        inputs = args.inputs
        if args.input.id == "layout":
            show_layout_inputs(inputs)
        try:
            faces = selected_faces(inputs)
            message = "Select planar faces to cut." if not faces else describe(*build_parts(faces, settings(inputs)))
        except (ExportError, RuntimeError) as error:
            message = str(error)
        inputs.itemById("status").text = message


class Execute(core.CommandEventHandler):
    def notify(self, args):
        app = core.Application.get()
        ui = app.userInterface
        try:
            inputs = args.command.commandInputs
            options = settings(inputs)
            faces = selected_faces(inputs)
            build_parts(faces, options)  # Fail before asking for a file name.
            dialog = ui.createFileDialog()
            dialog.title = COMMAND_NAME
            dialog.filter = "DXF files (*.dxf)"
            dialog.isMultiSelectEnabled = False
            dialog.initialFilename = f"{active_design().parentDocument.name}.dxf"
            if last_folder:
                dialog.initialDirectory = last_folder[0]
            if dialog.showSave() != core.DialogResults.DialogOK:
                return
            path = Path(dialog.filename)
            if path.suffix.lower() != ".dxf":
                path = path.with_name(path.name + ".dxf")
            last_folder[:] = [str(path.parent)]
            summary = export_faces(faces, path, options)
            ui.messageBox(f"Saved {path}\n\n{summary}", f"{COMMAND_NAME} v{VERSION}")
        except Exception as error:
            args.executeFailed = True
            args.executeFailedMessage = str(error)
            app.log(f"{COMMAND_NAME} v{VERSION} failed\n{traceback.format_exc()}",
                    core.LogLevels.ErrorLogLevel, core.LogTypes.FileLogType)
            ui.messageBox(f"{error}\n\nA copy of this error is in the Fusion application log.",
                          f"{COMMAND_NAME} v{VERSION}")


class Destroy(core.CommandEventHandler):
    def __init__(self, local_handlers):
        super().__init__()
        self.local_handlers = local_handlers

    def notify(self, args):
        for handler in self.local_handlers:
            if handler in handlers:
                handlers.remove(handler)
        self.local_handlers.clear()


class Created(core.CommandCreatedEventHandler):
    def notify(self, args):
        command = args.command
        inputs = command.commandInputs
        faces = inputs.addSelectionInput("faces", "Faces", "Pick the planar faces to cut. In the row layout, the pick order is the layout order.")
        faces.addSelectionFilter("PlanarFaces")
        faces.setSelectionLimits(1, 0)
        layout = inputs.addDropDownCommandInput("layout", "Layout", core.DropDownStyles.TextListDropDownStyle)
        for key, label in LAYOUTS.items():
            layout.listItems.add(label, key == "keep")
        orientation = inputs.addDropDownCommandInput("orientation", "Orientation",
                                                     core.DropDownStyles.TextListDropDownStyle)
        for key, label in ORIENTATIONS.items():
            orientation.listItems.add(label, key == "edge")
        inputs.addValueInput("spacing", "Spacing", "mm", core.ValueInput.createByString("5 mm"))
        inputs.addValueInput("sheet", "Sheet width", "mm", core.ValueInput.createByString("600 mm"))
        show_layout_inputs(inputs)
        inputs.addTextBoxCommandInput("status", "", "Select planar faces to cut.", 8, True)
        local_handlers = [InputsChanged(), Execute()]
        command.inputChanged.add(local_handlers[0])
        command.execute.add(local_handlers[1])
        destroy = Destroy(local_handlers)
        command.destroy.add(destroy)
        local_handlers.append(destroy)
        handlers.extend(local_handlers)


def command_panels(ui):
    """The Utilities > Make panel, and the Add-Ins panel as a fallback home."""
    workspace = ui.workspaces.itemById("FusionSolidEnvironment")
    found = []
    for tab in workspace.toolbarTabs if workspace else []:
        if tab.id == "ToolsTab":
            found = [tab.toolbarPanels.itemById("MakePanel"), tab.toolbarPanels.itemById("SolidScriptsAddinsPanel")]
    return [panel for panel in found if panel]


def clear_registration(ui):
    for panel in command_panels(ui):
        control = panel.controls.itemById(COMMAND_ID)
        if control:
            control.deleteMe()
    definition = ui.commandDefinitions.itemById(COMMAND_ID)
    if definition:
        definition.deleteMe()
    handlers.clear()


def run(_context):
    app = core.Application.get()
    ui = app.userInterface
    try:
        clear_registration(ui)
        panels = command_panels(ui)
        if not panels:
            raise ExportError("Could not find the Utilities > Make panel in the Design workspace.")
        definition = require(ui.commandDefinitions.addButtonDefinition(
            COMMAND_ID, COMMAND_NAME, "Export planar faces to one DXF for laser or plasma cutting.",
            str(ICON_FOLDER)), f"Could not register {COMMAND_NAME}.")
        created = Created()
        require(definition.commandCreated.add(created), f"Could not connect {COMMAND_NAME}.")
        handlers.append(created)
        for panel in panels:
            require(panel.controls.addCommand(definition), f"Could not add {COMMAND_NAME} to {panel.name}.")
        app.log(f"{COMMAND_NAME} v{VERSION} registered.")
    except Exception:
        app.log(traceback.format_exc())
        clear_registration(ui)
        raise


def stop(_context):
    clear_registration(core.Application.get().userInterface)
