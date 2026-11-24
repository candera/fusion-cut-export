# Cut Export for Autodesk Fusion

Status: active — v0.2.0 verified in Fusion with scripted tests; dialog and shop import not yet tried
Updated: 2026-11-22

## Outcome

A Fusion add-in that exports any number of planar faces to a single DXF for laser or plasma cutting. By default the faces keep their positions relative to each other, so an arrangement made with Fusion's Arrange command is exported as packed. Holes are separate contours. A second layout mode places each face in its own outside view and packs the parts in rows. It is done when a DXF from a real design cuts correctly on the Gweike M3 Pro (NexCut), the 30 W galvo (LightBurn), a Nova Labs laser (RDWorks), or a plasma table, with no cleanup after import.

## DXF or SVG

**DXF**, written as plain ASCII release 12. Reasoning:

- Every cutting package I expect to use reads DXF: LightBurn, NexCut, RDWorks, and plasma CAM such as SheetCAM. This is a general expectation about those programs, not yet tested with files from this add-in.
- DXF carries a real unit. The file declares millimetres (`$INSUNITS = 4`) and coordinates are mm. SVG leaves scale to the importer's DPI assumption (72, 90, or 96), the usual cause of cut parts that come out 25 % wrong.
- DXF has native lines, arcs, and circles. Plasma CAM wants real arcs and joined contours; SVG paths often arrive as Béziers or polylines.
- Release 12 needs only `LINE`, `ARC`, `CIRCLE`, and `POLYLINE`, which every reader supports, including older ones such as RDWorks. Newer-only entities (`LWPOLYLINE`, `SPLINE`) are avoided on purpose.
- Fusion's own sketch export (`Sketch.saveAsDXF`) writes geometry at its model position, so several faces would overlap. Laying out parts, choosing layers, and flattening each face from outside needed our own writer. I did not check whether newer Fusion releases export several faces to one DXF natively.

Add SVG only if a tool turns up that can't read DXF. The geometry layer (`flatcut.py`) would stay unchanged.

## Install and use

1. In Fusion, open **Utilities > Add-Ins > Scripts and Add-Ins**, choose **+ > Script or add-in from device**, select the [CutExport](CutExport/) folder, and turn on **Run**.
2. Open a design. The command is **Export Faces for Cutting** under **Utilities > Make**, and also in the Add-Ins panel.
3. Under **Faces**, pick the planar faces to cut. Faces on components, mirrored components, and tilted planes all work. Pick the broad face of each panel; its thickness is not used.
4. Choose a **Layout**:
   - **Keep arrangement** (default) draws every face in one shared view, so parts sit where they do in the model relative to each other. Use it after **Arrange** to keep a packing you chose. The view looks from +Z when the faces are flat in the XY plane, as Arrange results are, and otherwise from the side the first face points to. A face on the far side of a part, such as a bottom face, is drawn as seen through the part, so it lands exactly on the outline of the part's top face, and picking either side of an arranged part gives the same cut. The whole drawing is shifted so its lower left is at the origin. All the faces must be parallel; otherwise the command says which one is not. Orientation, spacing, and sheet width are hidden, since they don't apply.
   - **Lay out in rows** flattens each face on its own, as seen from outside its body, and packs the parts left to right in pick order. **Orientation** is *Align to longest edge* (turn each part so its longest straight outer edge is horizontal, by at most 90°) or *Keep face orientation*. **Spacing** between parts defaults to 5 mm and **Sheet width** to 600 mm. A part wider than the sheet is still placed, with a warning. Use this for faces that are not already laid out flat together.
5. The dialog lists each part's size, hole count, and any warnings.
6. Click **OK** and choose where to save. A message reports what was written.

### What the file contains

- Units are millimetres. The lower left of the drawing is at the origin.
- Outer contours are on layer `CUT_OUTER` (color red) and holes on `CUT_INNER` (color blue). Separate layers let the cutter do holes first.
- Lines, arcs, and circles are exact. Ellipses and splines become line segments within 0.005 mm of the true curve.
- In the row layout each part is the face as seen from outside its body, so a panel exported from its bottom face is mirrored relative to the top. In the default layout all parts share one view and are never mirrored relative to each other.
- Kerf is not compensated. Set it in the cutting software.

## Limits

- Planar faces only. Curved faces, bent sheet metal, and unfolding are out of scope.
- The row layout is simple packing in pick order. It is not nesting and does not rotate parts to save material. Use Fusion's Arrange for that and keep its result with the default layout.
- Engraving, scoring, and part labels are not exported.
- Tiny features below the cutter's kerf or the sheet's thickness limits are not checked.

## Verification

Test conditions: Autodesk Fusion 2706.1.17, macOS, 2026-11-22. The test scripts, outputs, and generated DXFs (`results/2026-11-22-v0.1.0/`) live in the author's local workspace and are not part of this repository.

**Measured**

- 9 offline tests of the geometry and DXF writer pass. The DXF is read back with `ezdxf` 1.4.5, which audits it with no errors. Run `python3 -m unittest discover -s tests`; the read-back tests are skipped without `ezdxf`.
- Live in Fusion, a scratch plate with filleted corners, a round hole, an elliptical hole, and an asymmetric notch was exported from its top and bottom faces. A second plate on a plane tilted 25° was exported as well. An independent script re-parsed each DXF and compared it with Fusion's own face area: all agree within 0.003 %. Loop counts match Fusion's. The top and bottom views are mirror images, as an outside view should be. In a combined export, the parts sat 5.000 mm apart and the extent matched the expected 205 × 105 mm.
- The tilted 60 × 40 mm plate exports at 60 × 40 mm, not foreshortened.
- All 8 broad faces of the Oiler Shelf from [Parametric Box Joint](https://github.com/candera/fusion-box-joints) were exported from nested occurrences, including the mirrored `Side(Mirror)`. Areas match Fusion's within 0.0003 %, and the combined export holds 8 parts.
- The command registers in Utilities > Make and the Add-Ins panel, and unregisters cleanly.

- v0.2.0, keep arrangement: on the Oiler Shelf's real `Arrange3` result, four parts were exported from their up-facing faces, then from their down-facing faces, then from a mix with a down-facing face picked first. Each part's position in the DXF matches its Fusion bounding box minus the drawing's lower left, within 0.05 mm, and the three exports agree. Selecting a shelf side panel (not parallel to the arranged parts) together with an arranged face gives the "not parallel" error naming `Side:1 face 1`. The row layout still passes every earlier check against v0.2.0 code.

**Not yet verified**

- The command dialog itself: selection, the Layout dropdown and its show/hide behavior, the status text, and the save dialog. The tests call `export_faces()` directly, as in the box-joint project.
- Import into LightBurn, NexCut, RDWorks, or any plasma CAM. Check that the units, the two layers, and the arcs survive.
- Physical parts.
- Keep arrangement on anything other than the shelf's `Arrange3` result, which lies flat in the XY plane. Parallel faces at different heights, or in a plane other than XY, follow the same code path but were not exercised.
- Windows (the manifest lists it, but only macOS was tested).

## Files

- [CutExport/CutExport.py](CutExport/CutExport.py) — the Fusion command: flattens faces and handles the dialog.
- [CutExport/flatcut.py](CutExport/flatcut.py) — Fusion-free 2D geometry, layout, and DXF writer.
- [tests/](tests/) — offline tests.

Live tests drove Fusion's local MCP server (`http://127.0.0.1:27182/mcp`) with [tools/fusion_mcp.py](https://github.com/candera/fusion-box-joints/blob/main/tools/fusion_mcp.py) from the box-joint project.

## Next step

Run the command from the toolbar on a real design, import the DXF into the cutting software that will cut it, and confirm size, layers, and arcs. If LightBurn or NexCut needs something different (for instance a part-per-layer scheme or joined polylines), adjust the writer. Then cut a test part in the intended material and measure it against the model.

## Sources and related work

- Intended cutters: Gweike M3 Pro (NexCut), Gweike 30 W galvo (LightBurn), Nova Labs CO2 lasers (RDWorks), and a CNC plasma cutter.
- [Parametric Box Joint](https://github.com/candera/fusion-box-joints) — panels from that project are natural inputs.
- Fusion API objects used: `BRepFace`, `BRepEdge`, `SurfaceEvaluator.getNormalAtPoint`, `CurveEvaluator3D.getStrokes`. The DXF output was checked with `ezdxf` rather than against Autodesk's specification text.

## License

[MIT](LICENSE)
